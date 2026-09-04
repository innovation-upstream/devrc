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
- Branch: `main`, behind `origin/main` by 1 commit
- Uncommitted changes (this session):
  - `scripts/mention-open.py` — 3 additions: imports `KNOWN_REPOS` + `GITHUB_ISSUE_URL`, rewrote `discover_repos()` to use static mapping as base + local overlay, added Pass 3 GitHub API fallback
  - `scripts/collector/known_repos.py` — NEW, 457-line static `{repo_name: "owner/repo"}` mapping (386 repos from `gh api user/repos --paginate` + local checkout overrides + lowercase normalization)
  - `scripts/regen-known-repos.sh` — NEW, regeneration script
- Other uncommitted changes (NOT from this session): `flake.lock`, `nix/programs/alacritty/default.nix`, `nix/system/apply-tmp-churn-retention.sh`
- Tests: all 107 pass (36 `test_mention_open.py` + 55 `test_mention_scan.py` + 16 `test_alacritty_hints.py`)
- Deploy status: LIVE from checkout (both files read from repo, not nix store)
- No PR open for this work

## Open investigations — live diagnosis state
(none — this session implemented a feature, no bugs mid-investigation)

## Next steps (ranked)
1. Commit and PR the mention-system-repos changes (3 files: `mention-open.py`, `known_repos.py`, `regen-known-repos.sh`)
   forcing: none
2. Test live by clicking `repo#N` references in Alacritty across several repos (especially ones not previously resolved: `promptver#10`, `naida-ai#42`, `vetr-api#50`)
   forcing: none
3. Consider adding `known_repos.py` to the `test_no_captured_text.py` ledger or a freshness test that warns when the static list is stale (currently no automated refresh)
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
