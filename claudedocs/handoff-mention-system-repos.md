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
🔴 **READ THIS BEFORE ACTING ON ANY OLDER COPY OF THIS DOC.** An earlier version of
this file ranked *"commit and PR … `known_repos.py`"* as step 1. **DO NOT.** That file
was a generated mapping built from `gh api user/repos`, which returns PRIVATE repos,
and committing it published 232 of them — 217 named nowhere else in the tree — to this
PUBLIC repository. `/resume` treats a ranked list as a work queue, which is exactly how
an instruction like that gets executed by a session that was not there for the incident.

- **PR #1283 is CLOSED, not merged, and its branch is deleted.** `main` never carried the
  mapping (`raw.githubusercontent.com/.../main/scripts/collector/known_repos.py` → 404).
- 🔴 **THE DISCLOSURE IS NOT CLOSED.** GitHub retains `refs/pull/1283/head`, so the file
  is still served from the closed PR to anyone. Deleting the branch did not remove it and
  no code change can. Remediation is a GitHub Support request to purge the PR; per
  `SECRETS.md`'s adjudicated precedent for this shape, a history rewrite is NOT
  recommended and the values should be treated as disclosed.
- **The rework lives on `fix/mention-open-namesakes`** (`scripts/regen-known-repos.py`,
  `scripts/mention-open.py`, `scripts/tests/test_regen_known_repos.py`). The mapping is
  now written to `~/.config/mention-open/known_repos.json`, mode 0600, per-host, outside
  every checkout, and a test asserts no tracked file parses as one — in BOTH test tiers,
  since the sandbox tier has no `.git` for `git ls-files`.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. Decide on the residual exposure: ask GitHub Support to purge `refs/pull/1283/head`, or
   record the decision to treat the names as disclosed. Nothing in the repo can do this.
   forcing: security
2. Gate `fix/mention-open-namesakes` on the MERGED tree (both tiers, the two nix check
   derivations built ONE AT A TIME), then PR it.
   forcing: gate
3. After it merges, `scripts/ship.sh` — `nix/programs/alacritty/default.nix` changed, so
   `gh` only reaches the click handler's PATH after a `home-manager switch` on BOTH hosts.
   Then run `scripts/regen-known-repos.py` once PER HOST: the mapping is per-host and
   nothing generates it on a timer.
   forcing: none

## Gotchas / decisions / dead-ends
- `--no-discovery` flag intentionally skips the static mapping (Pass 2 only). This is by design: the flag means "resolve only what the text itself carries."
- `clawgate#50` does NOT resolve — `clawgate` is a container inside `homelab-talos`, not a standalone GitHub repo. The `GITHUB_RE` scanner treats it as a repo name, finds no match, and the API fallback also finds nothing. This is correct behavior.
- Case normalization was necessary: GitHub repo names are case-insensitive (`ComfyUI` vs `comfyui`), so all keys in `KNOWN_REPOS` are lowercased. Local checkout overlays also add lowercase entries.
- The API fallback (`_gh_api_repo_search`) only fires for explicit `repo#N`, not bare `#N`. A bare `#N` needs context (tmux pane) to know which repo, and the API can't provide that.
- `known_repos.py` does NOT need nix deployment — `session-tailer.py` (the telemetry consumer) calls `scan_mention_spans(text)` without repos (detection only, no resolution), so it doesn't need the mapping.

## How to verify
```bash
# Static mapping resolution
python3 scripts/mention-open.py --print 'talos-infra#1065'  # → civitai/talos-infra URL
python3 scripts/mention-open.py --print 'devrc#123'         # → innovation-upstream/devrc URL
python3 scripts/mention-open.py --print 'promptver#10'      # → ZacxDev/promptver URL

# Case insensitive
python3 scripts/mention-open.py --print 'ComfyUI#100'       # → civitai/ComfyUI URL

# API fallback (repo not in static list)
python3 scripts/mention-open.py --print 'some-new-repo#1'   # → searches GitHub API

# Bare #N (needs tmux context)
python3 scripts/mention-open.py --print '#370'              # → clawgate + GitHub candidates

# Regeneration
scripts/regen-known-repos.sh
```
