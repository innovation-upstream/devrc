---
# No clawgate task — session had no CLAUDE_CODE_SESSION_ID
---
# Handoff: mention-system-repos — 2026-09-03

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Expand the mention system (`mention-open.py`) so clicking `repo#N` in Alacritty resolves against ALL repos the operator contributes to, not just those checked out locally in `~/workspace/`.

## State now
- Branch: `feat/mention-system-repos` (pushed, tracking `origin/feat/mention-system-repos`), one commit
  `a0ba4a1a` — `scripts/mention-open.py`, `scripts/tests/test_mention_open.py`,
  `scripts/collector/known_repos.py` (new), `scripts/regen-known-repos.sh` (new). No PR open yet.
- Integration worktree `~/workspace/devrc-mention-integ` (branch `integ/mention-merge`) =
  `origin/main` (`aba48864`) + the feature branch, merged cleanly. The two intervening commits
  touch espanso/keylog + `nix/home.nix` only.
- Unrelated dirty files left untouched in the base clone, deliberately NOT staged: `flake.lock`,
  `nix/programs/alacritty/default.nix` (selection→clipboard), `nix/system/apply-tmp-churn-retention.sh`,
  `nix/system/apply-nebula-workbench-relay.sh`, `output.txt`, `scripts/diagnose-{disk-accounting,nix-disk}.sh`.
- clawgate task: NOT resolved — `clawgate_handoff.sh resolve` exited 5 (0 tasks for this session;
  an unknown id also answers 200 with an empty array, so this is not a clean bill). No field written.

### Done this session (measured, not inferred)
1. **`regen-known-repos.sh` was a LOSSY generator — fixed.** It lowercased keys only for LOCAL
   checkouts, so running it as the handoff instructed DROPPED 33 lowercase aliases the committed
   mapping had (`comfyui`, `civitui`, `spm`, `gcodeide`, `idm-vton`, …) while its own header comment
   claimed "Keys are lowercased". `mention_scan._resolve_repo` does an EXACT dict lookup
   (`repos.get(repo)`) — no case folding anywhere — so those aliases are the only thing that makes
   `comfyui#100` resolve. Now emits both spellings for API-sourced repos too.
   Re-measured after the fix: keys before=454 after=420, **0 real repos lost, 0 values changed**;
   the only losses are 34 transient agent-worktree dir names and the only gain is `civitai-feed`.
2. **The generator also now skips LINKED WORKTREES** (`.git` is a FILE, not a directory). They share
   the base clone's remote so they add no owner, and they churned this generated file with names
   like `devrc-integ-1261`, `homelab-rsz`, `devrc-handoff-search` that nobody writes as a mention.
3. **PASS 3 (the GitHub API fallback) SHIPPED INERT — fixed.** Its jq filter lived in a Python string
   with NO f-prefix, so it compared `.name` against the literal text `"$name"` and selected nothing
   for any input, ever. Measured against the real jq binary:
   `jq -r '.items[] | select(.name == "$name") | .full_name'` → 0 lines, rc 0.
   `gh api --jq` accepts no `--arg`, so the fix moves the name match into Python and leaves the jq
   program a constant (`.items[].full_name`) with no interpolation — the whole class is gone.
   It also now takes the EXACT (case-insensitive) name match rather than `splitlines()[0]`: search is
   relevance-ordered, so the old first-row rule would have opened a stranger's repo under the
   operator's repo name had the filter ever worked.
4. **8 new tests** in `scripts/tests/test_mention_open.py`. Matrix measured, not assumed: **7 RED**
   against the pre-fix `_gh_api_repo_search` (restored into a scratch copy at
   `<scratchpad>/base/`), **green at HEAD**; the 8th (`…returns_nothing_when_gh_fails`) is labelled
   in its own docstring as an INVARIANT GUARD — it is green on the pre-fix code and never caught
   the bug.
5. Module docstring rewritten: it claimed owners come only from local git remotes or explicit
   `owner/repo`, which has been false since the static mapping landed. It now enumerates all four
   sources and their precedence.

### In flight
- **Two-tier gate.** Node tier: **PASS** (`scripts/gate.sh --tier both`, 1449 tests, floor 1367).
  Pytest tier: the dev-host run via `scripts/gate.sh` FAILED `exit=3` for a MISSING ENVIRONMENT
  (`logrotate` not on PATH) — not a code failure; re-running inside `nix develop` was still
  executing when this doc was written (~30 min, 4 workers live). **No pytest-tier verdict yet.**
- The `nix build .#checks.x86_64-linux.{pytests,nodetests}` tier (the one Tekton runs) has **NOT**
  been run at all yet — must be run ONE AT A TIME, in `~/workspace/devrc-mention-integ`.

## Open investigations — live diagnosis state
(none — this session implemented a feature, no bugs mid-investigation)

## Next steps (ranked)
1. Finish the two-tier gate on the MERGED tree at `~/workspace/devrc-mention-integ`:
   `nix develop <wt> -c bash scripts/run-tests.sh <wt>`, then
   `nix build .#checks.x86_64-linux.pytests` and `.#checks.x86_64-linux.nodetests` **separately**
   (a combined invocation produces false failures — store contention). Name the tier and base sha
   in the claim.
   forcing: gate
2. Open the PR for `feat/mention-system-repos` once the gate is green, then merge + `scripts/ship.sh`.
   Nothing blocks a merge in this repo today — protection is DECLARED off — so the local two-tier
   run is the only gate there is.
   forcing: none
3. Consider a freshness check for `scripts/collector/known_repos.py` — it is a generated snapshot with
   no automated refresh, and a repo created after the last regen resolves only via the (now working)
   API fallback. Cheapest form: a test that fails when the file is older than N days, or a timer that
   runs `regen-known-repos.sh` and opens a PR on a non-empty diff.
   forcing: none
4. Clean up the integration worktree when done: `git -C $DEVRC worktree remove ~/workspace/devrc-mention-integ`
   and delete branch `integ/mention-merge` (it holds a branch repo-globally until removed).
   forcing: none

## Gotchas / decisions / dead-ends
- `--no-discovery` flag intentionally skips the static mapping (Pass 2 only). This is by design: the flag means "resolve only what the text itself carries."
- `clawgate#50` does NOT resolve — `clawgate` is a container inside `homelab-talos`, not a standalone GitHub repo. The `GITHUB_RE` scanner treats it as a repo name, finds no match, and the API fallback also finds nothing. This is correct behavior.
- Case normalization was necessary: GitHub repo names are case-insensitive (`ComfyUI` vs `comfyui`), so all keys in `KNOWN_REPOS` are lowercased. Local checkout overlays also add lowercase entries.
- The API fallback (`_gh_api_repo_search`) only fires for explicit `repo#N`, not bare `#N`. A bare `#N` needs context (tmux pane) to know which repo, and the API can't provide that.
- `known_repos.py` does NOT need nix deployment — `session-tailer.py` (the telemetry consumer) calls `scan_mention_spans(text)` without repos (detection only, no resolution), so it doesn't need the mapping.

- 🔴 **The mention lookup is an EXACT dict hit.** `mention_scan._resolve_repo` does
  `repos.get(repo)` with no `.lower()` anywhere, so case-insensitivity lives ENTIRELY in
  `known_repos.py` carrying both spellings. Any future edit to the generator that emits one
  spelling silently breaks every lowercase mention, and every test still passes — the suite had
  no coverage of the mapping's key set.
- 🔴 **`monkeypatch.setattr(MO.subprocess, "run", fake)` patches the SHARED `subprocess` module.**
  `MO.subprocess` IS the module object, so the fake is installed globally for the test's duration —
  my own real-`jq` verification call inside such a test reached the FAKE and passed against the
  INERT original filter. Caught by running the same test against the pre-fix code and seeing it
  green. Fixed with `_REAL_RUN = subprocess.run` bound at import time, before any patch.
- The stubbed-`gh` tests are individually incapable of catching the original bug: with `gh` faked,
  a filter that selects nothing in production still passes. That is exactly how it shipped. Two
  tests now pin the SEAM instead of the component — one asserts the jq program contains neither
  `$` nor the searched name (so jq CANNOT be the selector), the other runs the EMITTED program
  through the real `jq` binary (skipped if `jq` is absent).
- `clawgate#50` still does not resolve, and that remains correct: `clawgate` is a container inside
  `homelab-talos`, not a standalone GitHub repo. The API fallback finds no exact name match either.
- `--no-discovery` still skips the static mapping AND Pass 3 by design — it means "resolve only what
  the text itself carries", which is what keeps `test_an_unresolvable_owner_exits_non_zero_and_explains`
  hermetic (no network in the suite).

## How to verify
```bash
# Static mapping (both spellings — the lookup is an exact dict hit)
python3 $DEVRC/scripts/mention-open.py --print 'talos-infra#1065'   # civitai/talos-infra
python3 $DEVRC/scripts/mention-open.py --print 'comfyui#100'        # civitai/ComfyUI
python3 $DEVRC/scripts/mention-open.py --print 'ComfyUI#100'        # same URL

# PASS 3 — was INERT before this session; these three are the regression
python3 $DEVRC/scripts/mention-open.py --print 'kubernetes#1'       # kubernetes/kubernetes
python3 $DEVRC/scripts/mention-open.py --print 'nixpkgs#1'          # NixOS/nixpkgs
python3 $DEVRC/scripts/mention-open.py --print 'zzz-not-a-real-repo-xyz#1'  # rc 1, refuses

# The generator must be LOSSLESS — regenerate and diff the key sets, not the file
$DEVRC/scripts/regen-known-repos.sh   # expect: no real repo lost, no value changed

# Tests
nix develop $DEVRC -c python3 -m pytest $DEVRC/scripts/tests/test_mention_open.py -q   # 44 passed
```
