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
**The 2026-09-03 resolver work is still SHIPPED AND VERIFIED** (PR #1291, squash `fd68d48c`)
— re-measured live 2026-09-04 and unchanged: mapping present on both hosts at
`~/.config/mention-open/known_repos.json` (0600, 18063 B workbench / 18045 B laptop),
`git ls-files | grep -c collector/known_repos` = 0, `talos-infra#1065` resolves,
`zzz-no-such-repo#1` refuses naming its reason.

**A SECOND effort is now IN FLIGHT on the same subsystem: detection widening + attribution.**
- A subagent is running in an isolated devrc worktree. **Its results were UNKNOWN when this
  doc was written** — no PR number, no gate result, no test matrix. Check for its PR before
  assuming anything about it.
- Claim held: `devrc-mention-attribution` (`claim-work --release devrc-mention-attribution`
  when its PR lands). It is ad-hoc, NOT derived from a rank in this doc's list.
- Duplicate sweep at dispatch: 30 open devrc PRs, none touching mention detection.

🔴 **`main` MOVED 5 commits mid-session** — `099771da` → `8ad0c3c1` (other sessions merging).
The brief handed the agent `099771da` as its regression base; that is still a valid ancestor
for a red-at-base matrix, but **the merged-tree gate must be run against current `main`**, not
against the base named in the brief.

⚠ Two hosts drifted apart: laptop is at `fd68d48c`, workbench + `origin/main` ahead of it.
Docs-only so far, but it re-opens the divergence class `ship.sh` exists to close.

### Operator decisions taken 2026-09-04 — settled, do not re-litigate
1. **Telemetry-wider, terminal-narrow.** The widened detection surface is for
   `session-tailer.py` only; the Alacritty hint keeps its narrow click-safe regex. This
   DELIBERATELY relaxes the module's "one set of regexes, can never drift apart" invariant,
   so the split must be explicit and pinned two-way by a test.
2. **Attribution before detection.** 92% of detections are unattributed bare `#N`.
3. **Enumerated allowlist only** for un-anchored wordy forms — following the module's own
   precedent for ClickUp's `DEV-123` form. No generic `\w+ \d+` patterns.
4. **On attribution failure, open the rofi TUI with fuzzy matching over all candidates**
   rather than refusing (operator, mid-session). Applies to the click path; see gotchas.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. **Exercise the real Alacritty click path** — still never done end-to-end.
   🔴 NEEDS A HUMAN (clicking raises windows — a `pkill`-class action for an agent).
   ⚠ **Now a MOVING TARGET**: the in-flight agent is changing `mention-open.py`'s failure
   paths (decision 4). Testing before that lands measures code about to be replaced — either
   do it now as a pre-change baseline and say so, or wait for the PR.
   Click `talos-infra#1065` (opens), `dashboard#12` (rofi picker), `#282828` (nothing).
   Plain left-click — the hint is `mouse.enabled = true` with no mods; `Ctrl+Shift+M` is the
   keyboard route.
   forcing: none
2. **Add a staleness signal for `~/.config/mention-open/known_repos.json`** (devrc;
   `scripts/regen-known-repos.py` + a test, or a systemd-user timer in `nix/home.nix`).
   RE-MEASURED 2026-09-04: still zero timers reference it — the one `list-timers` hit is
   `present-regen.timer`, which belongs to the `present` skill and is unrelated. Degrades to
   the API fallback rather than breaking.
   forcing: none
3. **Commit the workbench's `save_to_clipboard` WIP** in
   `nix/programs/alacritty/default.nix` (devrc, one file, 8 lines). Still uncommitted and
   still live on the workbench only.
   forcing: gate — `ship.sh` skipped the workbench with rc 7 over this exact file and will
   skip it again on the next ship that touches it.
4. **Review, gate and merge the in-flight detection/attribution PR**, then
   `claim-work --release devrc-mention-attribution`. Gate BOTH tiers on the merged tree
   against current `main` — nothing blocks a merge in this repo.
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

- 🔴 **MEASURED 2026-09-04 — one of the three "missed" forms was a FALSE PREMISE.**
  `<org>/<repo>#<number>` already works: `GITHUB_RE` carries an optional owner group and
  `mention-open.py --print 'innovation-upstream/devrc#1291'` resolves correctly. Of 791
  occurrences only 13 were uncovered — 3 dotted repo names (a documented deliberate
  exclusion) and the rest inside URLs, where Alacritty's URL hint correctly owns them.
  Do not "fix" this.
- 🔴 **`<repo> PR <number>` is DETECTED BUT MISATTRIBUTED, not missed** — and that is worse.
  81 occurrences, only 5 uncovered, because people write `PR #1291` and the `#1291` matches
  as bare ambiguous. The repo name two words to its left is thrown away. This reframes the
  whole task from "detect more" to "attribute better".
- 🔴 **THE DENOMINATOR TRAP — measure the right population.** A first sweep over all
  transcript fields saw 130M chars and produced wildly inflated gap counts. `session-tailer.py`
  scans **assistant text blocks only, non-sidechain** — not tool inputs, not tool results, not
  user messages. Honest population: **11,748 blocks / 5.99M chars per 24h**. Every figure
  below is the scoped one; the unscoped ones were discarded.
- **Measured baseline (24h, assistant text only):** 4,542 detections — 4,190 ambiguous bare
  `#N` (**92%**), 281 github, 71 clickup. Uncovered shapes, total/uncovered:
  `github.com/owner/repo/pull/N` 184/**184** · `/audit-pr N` 185/**185** · `task N` 179/164 ·
  `clawgate task N` 69/26 · `/issues/N` URLs 11/11 · `gh pr <sub> N` + `--repo` 14/14 ·
  `#task-N` 2/2. The PR-URL row is the standout: owner, repo and number are all present in
  the text, zero ambiguity, detected zero times.
- 🔴 **AN 81% PREFILTER WILL MAKE ANY NEW PATTERN INERT.**
  `session-tailer.py`'s `_MENTION_HINTS = ("#", "868")` short-circuits before the regex pass
  and **skips 9,468 of 11,748 assistant text blocks (81%)**. Every new shape (`audit-pr 1291`,
  `gh pr view 1291`, `clawgate task 370`) contains neither literal, so adding the regex alone
  ships a **completely dead** feature that passes any unit test calling `scan_mentions()`
  directly. It must be widened too — preferably DERIVED from the pattern ledger, and pinned by
  a mutation test that removes a hint literal and watches a reachability test go red.
- 🔴 **A git-sha pattern is catastrophic and was rejected outright** — a `[0-9a-f]{7,12}`
  probe returned **520,256** hits. Recorded so nobody re-proposes it.
- **The click path has THREE distinct dead-ends, not one** (`mention-open.py` ~lines 430-465),
  and decision 4 lands on each differently: (a) `len(matches) > PASS3_MAX_CHOICES` (8) refuses
  outright — its comment argues "a 100-row list is not a choice, it is a wall", which is
  exactly what fuzzy typing dissolves, so the cap moves AND that comment must move with it;
  (b) search ran, found nothing — fuzzy over the ~370-entry universe rescues typos;
  (c) bare `#N` with no `default_repo`.
- 🔴 **The fuzzy universe is `known_repos.json` — THE FILE FROM THE #1283 DISCLOSURE.** It
  holds private repo names. Displaying them in rofi on the operator's own screen is fine;
  they must never reach a log, a test fixture, an `activity.events` payload or a debug dump,
  and no test may read the real file.
- 🔴 **The #1283 disclosure — an operator-CLOSED decision, not an open item.** MOVED HERE
  from `State now` 2026-09-04 because that is a REPLACE heading and this block was being
  dropped by a routine status update; it is durable and belongs under an APPEND heading.
  Committing the generated mapping published 232 private repos (217 named nowhere else in
  the tree, 167 a client's) to this PUBLIC repo. PR #1283 is closed and its branch deleted,
  but GitHub retains `refs/pull/1283/head`, so the file is **still served from the closed
  PR** and no code change can alter that. Escalated with the measurement; **operator
  decision 2026-09-04: low severity, ignore — do not pursue a GitHub Support purge.**
  🔴 Do NOT re-raise this or re-rank it as work: it was seen, priced and declined. The
  PREVENTION is what remains in force — the mapping is untracked and
  `test_regen_known_repos.py` fails if any tracked file parses as one, in BOTH test tiers.
- **This session resolved no clawgate task** — `clawgate_handoff.sh resolve` exited 5
  (0 tasks). An unknown session id answers 200 with an empty array, so that is NOT evidence
  this session touched no task; no `clawgate-task:` field was written either way.

## How to verify
```bash
# The 2026-09-03 resolver work, on the deployed artifact (not a checkout)
python3 ~/workspace/devrc/scripts/mention-open.py --print 'talos-infra#1065'   # civitai/talos-infra
python3 ~/workspace/devrc/scripts/mention-open.py --print 'kubernetes#1'       # refuses: "at least N repositories are named"
python3 ~/workspace/devrc/scripts/mention-open.py --print 'zzz-no-such-repo#1' # refuses, names the reason

# The premise correction — this MUST resolve, it is not a gap
python3 ~/workspace/devrc/scripts/mention-open.py --print 'innovation-upstream/devrc#1291'

# The prefilter that makes new patterns inert — read it before adding any
grep -n '_MENTION_HINTS' ~/workspace/devrc/scripts/collector/claude/session-tailer.py

# The mapping is per-host, untracked, 0600
ls -l ~/.config/mention-open/known_repos.json
git -C ~/workspace/devrc ls-files | grep -c 'collector/known_repos'   # must be 0

# The disclosure guard, including its own positive control
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_regen_known_repos.py -q -k "published or detector_FIRES"

# In-flight work: is the agent's PR up, and is the claim still held?
gh pr list --repo innovation-upstream/devrc --state open --search 'mention in:title'
claim-work --list | grep mention-attribution

# Both hosts on the same sha (they were NOT at the time of writing)
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@192.168.50.155 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
