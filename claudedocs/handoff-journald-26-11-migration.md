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
- Branch: `chore/commit-staged-system-scripts` @ `2b799eb5`, rebased onto `origin/main` `03d7e0ad`.
- **PR #1412 OPEN** — https://github.com/innovation-upstream/devrc/pull/1412. Two files, both
  absent from `origin/main` (checked with `git cat-file -e origin/main:<path>`, not `git ls-files`):
  `nix/system/apply-journald-settings-migration.sh`, `scripts/diagnose-nix-disk.sh`.
- **The journald migration is APPLIED and VERIFIED on the workbench** — separate claims, both made:
  - `/etc/nixos/configuration.nix:745` now reads `services.journald.settings.Journal = { SystemMaxUse = "2G"; };`
  - live `/etc/systemd/journald.conf` = `[Journal]` / `Audit=keep` / `SystemMaxUse=2G`
    (`Audit=keep` is 26.11's new explicit default, not something this change set)
  - `/run/current-system` → `nixos-system-nixos-26.11pre1068949.dc5d91f84032`
  - The **laptop is UNTOUCHED and NOT VERIFIED** — it has its own `/etc/nixos`, and this
    session never looked at it. It will hit the same assertion on its next channel update.
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

## Open investigations — live diagnosis state

### The devrc base clone cannot fast-forward — PREDICTED, NOT YET MEASURED
- **Symptom (expected):** `git -C ~/workspace/devrc merge --ff-only origin/main` should refuse
  with "untracked working tree files would be overwritten by merge", which is exactly the
  state `CLAUDE.md` says makes `ship.sh` skip a host silently while it still looks healthy.
- **Observed (with values):**
  - Base clone is on `main` at `b508b684`; `origin/main` is `03d7e0ad`. Behind.
  - It holds two UNTRACKED files at paths `origin/main` now tracks:
    `nix/system/apply-nebula-relay.sh` (205 lines) and `nix/system/check-nebula-relays.sh` (317).
  - `origin/main`'s versions are 480 and 360 lines, landed by #1272.
- **Ruled out:** "the local copies are newer WIP worth keeping" — `git hash-object` on each
  matches the blob at commit `e7f2e45a` ("fix(nebula): read the RUNNING process…", 2026-09-04),
  an intermediate commit of the branch that merged as #1272. They are stale orphans, and
  committing them would have reverted ~318 lines including a `🔴 THIS RESTARTS THE MESH`
  warning they predate. via: command
- **Leading hypothesis:** deleting the two orphans clears the block and loses nothing, since
  both blobs are recoverable at `e7f2e45a`.
- **Next probe:** `git -C ~/workspace/devrc merge --ff-only origin/main` — non-destructive, it
  either fast-forwards or refuses. Run it BEFORE deleting anything, so the refusal is measured
  rather than assumed; then delete and re-run.

## Next steps (ranked)
1. **Read the gate verdict for `b8tu5owud`** (`<scratchpad>/gate-rebased.txt`) and post it to
   PR #1412. If merging, also run `nix build .#checks.x86_64-linux.pytests` and
   `…nodetests` ONE AT A TIME — that tier has not been run on this branch, and it is the one
   Tekton gates on. `IN FLIGHT: devrc#1412`. forcing: gate — `main` is protected in name only
   (`required_status_checks` absent, measured 2026-09-02), so nothing else blocks this merge.
2. **Unblock the base clone** — run the next-probe command above in `~/workspace/devrc`, then
   delete the two stale nebula orphans and re-run. forcing: regression — until this clears,
   `ship.sh` skips the workbench with `merge --ff-only`, and `CLAUDE.md` records two prior
   occurrences (2026-08-06, 2026-08-09) where a skipped host silently stopped receiving every
   change while still looking healthy.
3. **`/audit-pr 1412`** before merging — offered to the operator, not yet answered. forcing: none
4. **Fix the three deprecated-option warnings** surfaced by the 26.11 eval of
   `/etc/nixos/configuration.nix`: `services.dnsmasq.servers` → `.settings.server`,
   `services.gnome.tracker.enable` → `.tinysparql.enable`,
   `services.gnome.tracker-miners.enable` → `.localsearch.enable`. They still work today.
   forcing: none
5. **Apply the same journald migration to the laptop** when it next takes a channel update —
   `nix/system/apply-journald-settings-migration.sh` is idempotent and host-agnostic, but it
   has only ever been run on the workbench. forcing: none

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
  failed on `claudedocs/handoff-civitai-app-fleet.md:204` (`h.civit.ai`), a file this session
  never touched. Control on pristine `origin/main`: 18 passed. It existed at the branch point
  `4f49f5dc` and was fixed on `main` by #1405 — rebasing cleared it. Run the control before
  debugging your own diff.
- **Decision: the two nebula scripts were dropped from the PR**, not committed. See the
  investigation block above for the evidence that they are stale orphans.
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

# the base clone is unblocked (non-destructive: it fast-forwards or refuses)
git -C ~/workspace/devrc merge --ff-only origin/main
```
