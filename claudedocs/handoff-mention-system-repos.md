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
**SHIPPED AND VERIFIED — the mention work is done.** PR #1291 merged as squash `fd68d48c`
on `main`; both hosts converged and switched at that sha (checked directly, host-to-host,
because `ship.sh` printed `cross-host agreement NOT COMPARED` — its two legs ran separately).

- **What landed:** `scripts/regen-known-repos.py` (new), `scripts/mention-open.py`,
  `scripts/tests/test_mention_open.py`, `scripts/tests/test_regen_known_repos.py`,
  `nix/programs/alacritty/default.nix` (+`pkgs.gh`).
- **The mapping is NOT in the repo.** It is generated per host to
  `~/.config/mention-open/known_repos.json`, mode 0600 — 369 keys on the workbench, 370 on
  the laptop (they differ legitimately: different `~/workspace`). Nothing regenerates it on a
  timer.
- **Verified on the DEPLOYED artifact, both hosts** (not `--print` in a checkout):
  `talos-infra#1065` resolves; `comfyui#100` now offers the three real ComfyUI repos instead
  of a guaranteed 404; `kubernetes#1` refuses with "at least 65 repositories are named
  kubernetes (one page of results — there may be many more)" instead of silently opening one.
- Gate at merge: both nix check derivations built ONE AT A TIME on the merged tree at base
  `5793c0e5` — pytests 21215 collected / 0 failed, nodetests 1449 / 0 failed.
- ⚠ **The Alacritty CLICK PATH was never exercised** — only the resolver. See ranked step 1.

### 🔴 The #1283 disclosure — an operator-CLOSED decision, not an open item
Committing the generated mapping published 232 private repos (217 named nowhere else in the
tree, 167 a client's) to this PUBLIC repo. PR #1283 is closed and its branch deleted, but
GitHub retains `refs/pull/1283/head`, so the file is **still served from the closed PR** and
no code change can alter that. Escalated with the measurement; **operator decision 2026-09-04:
low severity, ignore — do not pursue a GitHub Support purge.** 🔴 Do NOT re-raise this or
re-rank it as work: it was seen, priced and declined. The PREVENTION is what remains in force
— the mapping is untracked and `test_regen_known_repos.py` fails if any tracked file parses
as one, in BOTH test tiers.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. **Exercise the real Alacritty click path** — the one thing the ship did not prove. Click a
   `talos-infra#1065` (expect: opens), a `dashboard#12` (expect: a rofi PICKER, several
   owners, nothing auto-opened) and a `#282828` (expect: nothing). Untested end-to-end:
   Alacritty's own regex matching, the hint firing, argv arrival, `xdg-open`/rofi launching.
   🔴 NEEDS A HUMAN — clicking raises windows and takes the operator's screen, which is a
   `pkill`-class action for an agent. Not runnable by a session on its own.
   forcing: none
2. **Add a staleness signal for `~/.config/mention-open/known_repos.json`** (devrc; would
   touch `scripts/regen-known-repos.py` + a test, or a systemd-user timer in `nix/home.nix`).
   Measured 2026-09-04: zero systemd timers reference it, and no test checks its age. A repo
   created after the last regen falls through to the API fallback, which now works — so this
   DEGRADES rather than breaks, which is why it is not urgent.
   forcing: none
3. **Commit the workbench's `save_to_clipboard` WIP** in `nix/programs/alacritty/default.nix`
   (devrc, one file). It exists ONLY in the workbench working tree, is LIVE there, and is in
   no commit — it blocked this session's `ship.sh` (rc 7) and will block the next ship that
   touches that file. Preserved copies if it is ever lost:
   `<scratchpad>/alacritty-selection-WIP.patch` and `…/alacritty-default.nix.BEFORE`.
   forcing: none

## Gotchas / decisions / dead-ends
- `--no-discovery` flag intentionally skips the static mapping (Pass 2 only). This is by design: the flag means "resolve only what the text itself carries."
- `clawgate#50` does NOT resolve — `clawgate` is a container inside `homelab-talos`, not a standalone GitHub repo. The `GITHUB_RE` scanner treats it as a repo name, finds no match, and the API fallback also finds nothing. This is correct behavior.
- Case normalization was necessary: GitHub repo names are case-insensitive (`ComfyUI` vs `comfyui`), so all keys in `KNOWN_REPOS` are lowercased. Local checkout overlays also add lowercase entries.
- The API fallback (`_gh_api_repo_search`) only fires for explicit `repo#N`, not bare `#N`. A bare `#N` needs context (tmux pane) to know which repo, and the API can't provide that.
- `known_repos.py` does NOT need nix deployment — `session-tailer.py` (the telemetry consumer) calls `scan_mention_spans(text)` without repos (detection only, no resolution), so it doesn't need the mapping.

- 🔴 **`gh api user/repos` RETURNS PRIVATE REPOS.** That one fact is the whole incident: a
  generator built on it wrote 232 of them into a file that got committed to a PUBLIC repo,
  and all four content gates were structurally blind — they scan JSON/JSONL/HTML/TXT and
  hostnames, so a `.py` dict of repo names matched none of them. Any future "list my repos"
  feature inherits this.
- 🔴 **A pre-push scan is worth more than an audit pass.** Nine audit rounds did not catch
  `homelab-infra` — a real private repo name — sitting in a test docstring. A scan of every
  name the branch ADDS against `gh api user/repos`, with a positive control proving it can
  fire, caught it in seconds at the moment of pushing. Keep the control: a scan reporting
  zero private repos known is indistinguishable from a clean result.
- 🔴 **"I mutated it and nothing changed" is only evidence about the inputs you varied.** A
  clause here was deleted as "measured redundant" on a fuzz that varied only lowercase names,
  when CASE was the dimension that commit had just introduced. It was load-bearing. The
  clause is restored and its comment now says so. Two harnesses later disagreed on the
  magnitude by ~2x, so no count is quoted anywhere — neither harness is committed, so no
  figure in the source could be re-derived from the tree.
- 🔴 **A guard can be inert in ONE test tier.** The disclosure guard first used `git ls-files`
  and was silently blind in the sandbox tier, which builds from a store copy with no `.git`.
  Separately, a workspace guard asserted an EMPTY workspace yields `{}` — which is also what
  the broken code yields where `HOME=$TMPDIR/home`, so it died on the dev host and survived in
  the tier the merge is gated on. Both now assert a POSITIVE (something IS found) and are
  verified red under both a real and an empty HOME.
- **`ship.sh` will not stash, and that is correct** — it skipped the workbench (rc 7) rather
  than touch an uncommitted file. Resolving it meant preserving the WIP, taking upstream,
  shipping, re-applying, and re-switching so the operator's live setting came back. Taking
  upstream alone would have silently removed a setting that was live on the machine.
- **A squash merge never makes the branch head an ancestor of `main`** — #1291 was verified by
  CONTENT (`git ls-tree origin/main -- <the new files>`), never by `merge-base --is-ancestor`.

## How to verify
```bash
# The deployed artifact on either host (not a checkout)
python3 ~/workspace/devrc/scripts/mention-open.py --print 'talos-infra#1065'   # civitai/talos-infra
python3 ~/workspace/devrc/scripts/mention-open.py --print 'kubernetes#1'       # refuses: "at least N repositories are named"
python3 ~/workspace/devrc/scripts/mention-open.py --print 'zzz-no-such-repo#1' # refuses, names the reason

# The mapping is per-host, untracked, and 0600
ls -l ~/.config/mention-open/known_repos.json
git -C ~/workspace/devrc ls-files | grep -c 'collector/known_repos'   # must be 0

# The disclosure guard, including its own positive control
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_regen_known_repos.py -q -k "published or detector_FIRES"

# Both hosts on the same sha
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@192.168.50.155 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
