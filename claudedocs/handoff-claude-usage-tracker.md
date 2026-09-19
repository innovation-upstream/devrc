# Handoff: claude-usage-tracker — 2026-09-19

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. `scope-absent`/`scope-empty` means nothing is recorded yet: ordinary, not an
error. Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Track every Claude account's usage quota (session/weekly %, reset times, credits)
without flipping accounts and guessing — a pure MV3 extension that snapshots each
account on claude.ai open, toasts with thresholds, and shows all accounts in a popup.
Scope + live recon (API schema, endpoints): `claudedocs/proposal-claude-usage-tracker.md`
(rides PR #1792).
- **closing-condition:** `check` — PR #1792 merged; `scripts/ship.sh` converges both
  hosts (rc 0, per-host lines all clean) with `~/.local/share/claude-usage-ext/manifest.json`
  present on the workbench; and the extension is verified on the real click path
  (open claude.ai → toast fires; icon badge shows session %; popup lists accounts).
  Verdict rule: ADDRESSED ⇒ arc CLOSED; NOT ⇒ name the one item.

## State now
- Branch / PR: `claude-usage-tracker` → **PR #1792** (OPEN, MERGEABLE, CLEAN at
  handoff time; `tekton/devrc-*` are ADVISORY and were still pending when snapshotted —
  read the failing test's name before debugging any red; measured ~42% noise rate).
  All work sits in worktree `~/workspace/devrc-claude-usage` (branch off `c5fc4a95`),
  single commit `7e1bc585` (22 files, +2921).
- DONE this session (all first-hand verified):
  - Full extension: `scripts/claude-usage/extension/` (content_probe.js page-context
    fetch, service_worker.js orchestration/storage/toasts/badge/alarms, popup,
    lib/normalize.js + lib/timefmt.js, icons).
  - 87 node:test tests across 8 suites (`scripts/claude-usage/tests/`); registered in
    `scripts/run-node-tests.sh` SUITES as `scripts/claude-usage/tests|7|81`.
  - `nix/home.nix`: extracted `mkUnpackedExtensionDeploy` from the discord-embed
    activation (that block's own comment set the third-extension threshold); added
    `claudeUsageExtension` (var `cue`). The `dee` instance's rendered activation is
    **byte-identical** to the deployed generation's (verified pre/post build against
    `/nix/store/kwa1dzgb1bh7b3ql4v8rp3lkis20ymlp-home-manager-generation/activate`).
  - Verification run on the branch: node tier 1536/1536 `RESULT: PASS`; pytest gates
    captured-text / captured-markup / client-hostnames / public-ips all green
    (194+33 passed); `nix-instantiate --parse nix/home.nix` OK; activation package built.
  - Mutation battery: M1 credentials-drop (12/1), M2 reprobe-period (8/1), M3a
    reserved-keys (11/2), M3b weekly-dedup isolated (12/1), M4 toast-dedup (11/1) —
    all killed; restores cmp-verified; final sweep 85/85 → later 87/87.
  - Adversarial review round 1: 5 findings, ALL fixed + re-verified (see Defects).
  - Recon measured live: `GET /api/organizations/<org-uuid>/usage` (full schema in the
    proposal), `GET /api/organizations` for discovery, cookie-auth, page-context fetch.
- IN FLIGHT: nothing started beyond the PR itself.
- Deploy/verify status: **NOT deployed, NOT verified in a real browser** — the extension
  exists only in the repo and (post-merge) in the nix store. The click-path verification
  is the closing condition and has never run.
- Main checkout (`~/workspace/devrc`) state: `main` behind 2; dirty `nix/pkgs/lang/
  default.nix` (NOT this session's — leave it); untracked `claudedocs/scope-chief-*`
  ×2 (another session's — leave them). The stale untracked duplicate of the proposal
  was REMOVED this session (canonical copy rides the PR; it would have blocked the
  post-merge ff-merge).
- Worktree + scratch to clean up post-merge: `~/workspace/devrc-claude-usage`
  (`git worktree remove`), `/tmp/opencode/claude-usage*`, `/tmp/opencode/cu-mut`,
  `/tmp/opencode/dee-*`.

## Next steps (ranked)
1. Merge PR #1792 — read the Tekton checks first; a red is weak evidence (measure the
   failing test against the diff before believing it).
   `forcing: user` — the operator commissioned and approved this feature's dispatch.
2. Pre-merge guard on the main checkout: `git -C ~/workspace/devrc status` must NOT list
   `claudedocs/proposal-claude-usage-tracker.md` (removed already) — if any NEW untracked
   file collides with the merge, ff-merge refuses; move it aside first.
   `forcing: gate` — `ship.sh`'s `merge --ff-only` skips a host it cannot fast-forward.
3. Deploy: `scripts/ship.sh` from the main checkout (converges both hosts; it runs
   `home-manager switch`, which installs `~/.local/share/claude-usage-ext/`). Read every
   per-host line, not just the verdict.
   `forcing: user` — the feature is unusable until deployed.
4. One-time manual step (cannot be automated): brave://extensions → Developer mode →
   Load unpacked → `~/.local/share/claude-usage-ext` — on the workbench, and on the
   laptop if used there.
   `forcing: user` — nix cannot register an unpacked extension; only the operator can.
5. VERIFY the real click path (the closing condition): open claude.ai in Brave → toast
   summarizing the current account fires; toolbar badge shows session %; click → popup
   lists all known accounts with reset countdowns + staleness. If anything misbehaves,
   capture the SW console + a screenshot before diagnosing.
   `forcing: user` — no test can prove the extension works inside Brave; this is the
   arc's own closing condition.
6. Cleanup: after merge, `git -C ~/workspace/devrc worktree remove
   ~/workspace/devrc-claude-usage`, delete the branch, and clear the /tmp/opencode
   scratch dirs listed in State now.
   `forcing: none`

## Defects (batched)
- Review round 1 (all fixed in `7e1bc585`, suites re-run green after): stale proposal
  status line → updated to IMPLEMENTED; `weekly.lockedReason` unconsumed → weekly-lock
  alert added + pinned (reviewer's premise that the API lacks the field was WRONG — recon
  shows `locked_reason` on every window); `badgeFor(null, truthy-org)` TypeError →
  guarded + pinned; `pickActiveOrg` ran on the RAW org list (could pick an org that
  validation drops) → now picks from the validated list, `isActive` carried through
  `validateOrgs`, F4 pin added; `normalizeUsage` docstring claim narrowed to write+read
  paths. One follow-on test bug (missing `ORG_C` const) fixed same session — 87/87.

## Gotchas / decisions / dead-ends
- The opencode dispatch (GLM-5.3-Flash) died at a PERMISSION REJECTION: its mutation
  battery used `rm -rf` + `sed -i`, the headless permission gate auto-rejected, and the
  run abandoned WITHOUT committing (log frozen; deliverables 1–2 done, 3–4 absent).
  Lesson for future briefs: verification commands must avoid `rm -rf`/`sed -i`-style
  self-mutation — the agent cannot approve anything.
- nix `''` string escaping (cost three failed renders): `$${var}` is LITERAL — no
  interpolation; `$$` stays literal (bash PID); the idiom for a bash `$name` in a
  parameterized activation script is `''$${var}Name`. Proven via `nix-instantiate
  --eval` on a minimal string, then by byte-diff of the rendered activation.
- The flake only sees git-TRACKED files: `nix build …#homeConfigurations.zach.activationPackage`
  failed with "path …/scripts/claude-usage/extension does not exist" until `git add`
  — the documented flake trap, hit live.
- Mutation hygiene: one missed restore-cp CONTAMINATED the next mutant's result (M3b's
  first run was red from M3a's residue). Restore + `cmp` after EVERY mutate, and isolate
  before re-running.
- `scripts/scoped-tests.sh` refuses shared-surface diffs (`nix/**`, the runners) — this
  change touches both, so full tiers were the only option.
- browser-bridge has NO network-capture op (fixed allowlist; CDP limited to
  eval/screenshot/input/emulate). Recon workaround: in-page `fetch`/XHR monkey-patch +
  hash-router re-trigger of `#settings/usage`. A `net` op is proposed in the scope doc.
- `pgrep -f "opencode run"` matched my OWN shell and reported the dead run ACTIVE —
  resolve PIDs via /proc before believing liveness.
- The home-manager profile symlinks THROUGH `~/workspace/devrc`
  (`~/.local/state/nix/profiles/home-manager` → `home-manager-795-link` in the repo tree
  → the store generation) — `os.path.realpath` lands inside the repo; use the store path
  directly.
- `homeConfigurations.zach.activationPackage` needs `--impure` (`serverMode` uses
  `builtins.pathExists`).

## How to verify
- Gate on the branch (until merged): `nix develop ~/workspace/devrc-claude-usage -c zsh -c 'scripts/run-node-tests.sh'`
  → `RESULT: PASS (exit=0)`, claude-usage row `tests=87 floor=81`.
- Post-merge, in the main checkout: same command against `~/workspace/devrc`.
- Deployed artifact exists after switch: `ls ~/.local/share/claude-usage-ext/manifest.json`.
- Real click path (closing condition): claude.ai open → toast; badge %; popup account list.
- dee byte-identity (only if the helper is ever re-touched): build
  `nix build ~/workspace/devrc#homeConfigurations.zach.activationPackage --impure`,
  extract the `deeSrc=…` block from `result/activate`, diff against the deployed
  generation's activate script — must be byte-identical.
- Content gates if fixtures change: `nix develop . -c python3 -m pytest
  scripts/tests/test_no_captured_text.py scripts/tests/test_no_captured_markup.py
  scripts/tests/test_no_client_hostnames.py scripts/tests/test_no_public_ips.py -q`.
