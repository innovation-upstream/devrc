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
- **THE DISCLOSURE IS AN OPERATOR-CLOSED DECISION, NOT AN OPEN ITEM.** GitHub
  retains `refs/pull/1283/head`, so the leaked mapping is still served from the
  closed PR to anyone; deleting the branch did not remove it and no code change
  can. It was escalated with the measurement (232 private repos, 217 named
  nowhere else in the tree, 167 a client's) and the operator's decision on
  2026-09-04 was: **low severity, ignore — do not pursue a GitHub Support
  purge.** 🔴 Do NOT re-raise this as a finding or re-rank it as work: it was
  seen, priced and declined. What remains in force is the PREVENTION — the
  mapping is untracked and a test fails if any tracked file parses as one.
- **The rework lives on `fix/mention-open-namesakes`** (`scripts/regen-known-repos.py`,
  `scripts/mention-open.py`, `scripts/tests/test_regen_known_repos.py`). The mapping is
  now written to `~/.config/mention-open/known_repos.json`, mode 0600, per-host, outside
  every checkout, and a test asserts no tracked file parses as one — in BOTH test tiers,
  since the sandbox tier has no `.git` for `git ls-files`.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. Nothing outstanding. PR #1291 merged and shipped to both hosts; run
   `scripts/regen-known-repos.py` on any host that has not generated its
   mapping yet (it is per-host, and nothing generates it on a timer).
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
