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
**BOTH efforts are now SHIPPED.** The 2026-09-03 resolver (#1291, `fd68d48c`) and the
2026-09-04/05 detection-widening + attribution work (#1313, squash `3c324156`).

**#1313 — merged and VERIFIED ON THE DEPLOYED ARTIFACT, both hosts.**
- Ladder: round-1 full audit → 5 findings → fix round → round-2 delta audit → **safe to
  merge, no 🔴**. Round 2 re-ran the two claims that were self-reported rather than code (the
  17/17 mutation sweep, independently re-run with its positive control killed and sources
  restored to identical hashes; and the 219 floor, re-derived from a live 230-count through
  the gate's own formula).
- Gated sha `7d94c568` **was** the merged tree (`origin/main` `f887e958` an ancestor of it),
  so what was audited is byte-identical to what merged. All four legs green: dev-host pytest
  21,642 collected / 0 failed, dev-host node 1,449 / 0, plus both nix derivations built ONE
  AT A TIME.
- `ship.sh` converged both hosts at `3c324156` and **compared** them (not "NOT COMPARED").
- Deployed-artifact checks (not a checkout): `mention_scan.py` → `644jij…` (was `arkxlc…`),
  `session-tailer.py` → `cgxllq…` carrying the attributed dedupe key, and
  `_MENTION_HINTS = MS.mention_hints(MS.PROFILE_TELEMETRY)` — the inert-prefilter fix at the
  TELEMETRY profile (deriving at the default returns the old two literals and LOOKS correct).
- **No consumer restart was needed, and that was checked not assumed**: the mention work runs
  under `claude-activity-source.timer`, which is `oneshot` and fires ~5-minutely, so each run
  is a fresh interpreter. `activity-collector.service` has been up 8 days and the switch did
  not restart it — it is the spool shipper and this PR does not touch it.

🔴 **THE HOSTS AGREE ON GIT AND NOT ON ARTIFACT.** `ship.sh` said so explicitly:
`nix/programs/alacritty/default.nix` is dirty AND read at eval time, so the workbench's
generation is `origin/main` PLUS the uncommitted `save_to_clipboard` WIP. The laptop's is
not. Git parity is not host parity — see ranked item 3.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. **Exercise the real Alacritty click path** — STILL the one thing never done end-to-end,
   and now more valuable than before because #1313 changed it. Plain left-click (the hint is
   `mouse.enabled = true` with no mods; `Ctrl+Shift+M` is the keyboard route):
   `talos-infra#1065` (opens), `dashboard#12` (rofi picker, now with an explanatory line
   above the list naming the clicked text, the row count and the mapping's age), `#282828`
   (underlines; since #1328 it opens NO picker and shows a named toast saying it looks like a
   colour literal). 🔴 **The widest-blast-radius change to check deliberately:** a bare `#N` clicked
   in a pane with NO resolvable repo now shows a ~370-row fuzzy picker with the clawgate task
   first, where it previously opened the clawgate task directly. Type to narrow. If that is
   wrong in practice it is a one-line ordering change.
   🔴 NEEDS A HUMAN — clicking raises windows, a `pkill`-class action for an agent.
   forcing: none
2. ~~**Add a staleness signal for `~/.config/mention-open/known_repos.json`**~~ — **DONE** in
   PR #1328. `mention-open.py` now measures the mapping's age (`mapping_age_days`) and
   surfaces it in BOTH places the operator can see it: the note above the fuzzy picker
   (`universe_note`) and the refusal body (`staleness_note`, past `STALE_MAPPING_DAYS = 7`).
   🔴 **A SIGNAL, NOT A REPAIR, AND THAT WAS THE CHOICE.** A systemd-user timer was the other
   option and was rejected: `regen-known-repos.py` shells out to `gh api user/repos` and
   REFUSES below its 25-repo floor with exit 3, so a host without `gh auth` would take a
   failing unit plus a failure toast on every fire — a permanently-red timer, which trains
   everyone to ignore it. The signal instead fires at the exact moment the staleness bites:
   the click that did not resolve. Re-measured 2026-09-05: still zero timers reference the
   generator. Pinned at four ages (0, 6, 8, 90 days) by `test_the_mapping_age_is_measured_at_
   FOUR_points`, and mutation-verified (K32).
   forcing: none
3. **Commit the workbench's `save_to_clipboard` WIP** in
   `nix/programs/alacritty/default.nix`. Now the ONLY substantive difference between the two
   hosts' built generations. MEASURED 2026-09-05: it did NOT block this ship (workbench was
   behind 1 / ahead 0, and the incoming commit does not touch that file), so `--ff-only`
   succeeded — but it stays a latent rc 7 for the next ship whose incoming commits DO touch
   it, and meanwhile the two hosts differ.
   forcing: gate — `ship.sh` already skipped this host once with rc 7 over this exact file.
4. ~~Review, gate and merge the in-flight detection/attribution PR~~ — **DONE** (#1313 merged
   `3c324156`, claim `devrc-mention-attribution` released). Kept numbered so ranks 1-3 do not
   re-point.
   forcing: none
5. **Fix `scripts/collector/README.md:66-69`** (devrc, one file) — round 2's only 🟡, left
   unfixed ON PURPOSE so the merged artifact stayed byte-identical to the audited one. The
   README still promises IN BOLD that "what the terminal underlines and what the telemetry
   records cannot drift", and gives that as the reason for the module's location — the exact
   invariant #1313 deliberately retired (`mention_scan.py:12` now opens "🔴 THE TWO CONSUMERS
   NO LONGER SHARE ONE PATTERN SET"). Lines 59-60 of the same block also list the emitted
   payload fields without `repo` / `repo_source`. Risk: a maintainer reads it, believes the
   click surface is structurally protected, and adds a pattern as `_BOTH` — the thing the
   split exists to make a deliberate act.
   forcing: none

## Gotchas / decisions / dead-ends
- `--no-discovery` flag intentionally skips the static mapping (Pass 2 only). This is by design: the flag means "resolve only what the text itself carries."
- `clawgate#50` does NOT auto-resolve — `clawgate` is a container inside `homelab-talos`, not a standalone GitHub repo. The `GITHUB_RE` scanner treats it as a repo name and finds no match. Since #1328 that is not a refusal: it opens the fuzzy repo picker over the local universe, with a note above the list naming the clicked text.
- Case normalization was necessary: GitHub repo names are case-insensitive (`ComfyUI` vs `comfyui`), so all keys in `KNOWN_REPOS` are lowercased. Local checkout overlays also add lowercase entries.
- 🔴 **THE API FALLBACK IS DELETED — do not reason from it.** `_gh_api_repo_search` / `gh api search/repositories` was removed in PR #1328: measured at **4.3s through `--print` and ~10s through the real hint path**, essentially all network wait, answering `dashboard#12` with strangers' repositories. `mention-open.py`'s docstring carries the full record. THE CLICK PATH NOW MAKES NO NETWORK CALL, pinned by a two-way ledger of every command the resolution path may spawn (`test_the_resolution_path_spawns_ONLY_these_local_commands`) — keyed on the VERB PATH, `("git", "remote", "get-url")`, because a two-word key admitted `git remote update`, which fetches.
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
- ~~**The click path has THREE distinct dead-ends**~~ — **ALL THREE CLOSED in #1328, and the
  furniture this bullet described is GONE.** `PASS3_MAX_CHOICES` (the cap of 8) no longer
  exists: `pick()` runs rofi with `-matching fuzzy`, which turns the wall into a narrowing,
  and the comment arguing for the cap was replaced by one forbidding its return. The
  namesake SEARCH no longer exists either (see the API-fallback bullet above), so "search
  ran, found nothing" is not a state. What remains: (a) an unresolvable `repo#N` → the fuzzy
  universe picker with an explanatory note; (b) a bare `#N` with no `default_repo` → clawgate
  FIRST with the universe appended below it; (c) a SIX-DIGIT `#N` → a named toast, not a
  picker, because on this host six digits is always a colour literal and every offered row
  would 404.
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

- 🔴 **Operator decisions taken 2026-09-04 — settled, do not re-litigate.** MOVED HERE from
  `State now` 2026-09-05 because that is a REPLACE heading and a routine status update was
  dropping the block; it is durable and belongs under an APPEND heading. (1) **Telemetry-wider,
  terminal-narrow** — the widened detection surface is for `session-tailer.py` only; the
  Alacritty hint keeps its narrow click-safe regex. This deliberately relaxes the "one set of
  regexes, can never drift apart" invariant, so the split must stay explicit and pinned
  two-way by a test. (2) **Attribution before detection** — 92% of detections were
  unattributed bare `#N`. (3) **Enumerated allowlist only** for un-anchored wordy forms,
  following the module's own precedent for ClickUp's `DEV-123` form; no generic `\w+ \d+`
  patterns, and no git-sha pattern ever (a `[0-9a-f]{7,12}` probe returned 520,256 hits).
  (4) **On attribution failure, open the rofi TUI with fuzzy matching** rather than refusing.
  All four are implemented and shipped in #1313.
- 🔴 **A "merged tree" that is the BRANCH HEAD is a no-op merge, and it hid a real red.**
  #1313's first gate claimed merged-tree `316ccf74` — but that was the branch head and
  `cc409f82` the merge base, so the gate never saw `main`. When the fix round built a TRUE
  merge it went **RED**, and `main` moved to `f887e958` mid-run forcing a second merge whose
  new `main` touched a test file the branch also modifies. That is the disjoint-file merge
  hazard arriving in practice. **Check `merge-base --is-ancestor origin/main <head>` before
  believing any "merged tree" claim.**
- 🔴 **The migration cost of putting `repo` in the dedupe key turned out to be ZERO, and the
  reasoning is the reusable part.** Rather than accepting a one-time re-emit of every
  already-emitted mention, the fix read the DEPLOYED `collect_mentions` out of
  `git show origin/main:` and established it takes no `repos` argument — so every key already
  on disk was written unattributed and still matches under a CONDITIONAL suffix
  (`platform:raw` unattributed, `platform:raw@owner/repo` attributed). Only rows that
  genuinely gain an attribution re-emit. Collision-safety (`@` cannot occur in `raw`) was
  verified by reading every character class AND by fuzzing 200,000 random strings over an
  alphabet containing `@` — 0 counterexamples.
- 🔴 **A ladder round can be ENDED on a stated criterion, and that must be WRITTEN DOWN.**
  No round 3 was run. Round 2 found one 🟡 (the README) and six 🟢 — no defect in the
  payload's BEHAVIOUR — and a round auditing prose fixes generates prose findings
  indefinitely, which is the non-terminating shape. Recorded explicitly because "ended on
  that reasoning" and "converged cleanly" are indistinguishable in a report otherwise.
- **Private repo NAMES in this public tree: measured, and larger than the 3 that were
  reported.** A tree-wide scan found **35 distinct private repo names across 2,364
  occurrences**, all pre-existing and none introduced by #1313. Grouped by owner, 14 of them
  belong to the account holding 167 private repos (the client's, per the #1283 write-up),
  1,619 occurrences. Much of it is DELIBERATE — `CLAUDE.md` names client repos and env
  handles so an agent can route to the right checkout. 🔴 **No gate covers this class**: all
  four content gates read `git ls-files` and scan JSON/JSONL/HTML/TXT plus hostnames and IPs,
  so a repo name in a markdown sentence matches none of them — the same structural blindness
  that let #1283 through. **Suggested (not built): a RATCHET** pinning the current set and
  failing when a NEW private name appears; that churns nothing and closes the actual hazard,
  which is the next bulk dump rather than the names already present. ⚠ The scan's positive
  control held (private list non-empty, 233/149); its negative control was near-vacuous.
- 🔴 **`--print` NOW REFUSES an unresolvable reference, and that reverses the line this bullet
  used to carry.** It read "`--print` above the retired namesake cap now prints every namesake
  and exits 0"; #1328 deleted the namesake search, so there are no namesakes to print.
  `--print dashboard#12` exits 1 with a named reason instead of exit 0 carrying strangers'
  repository URLs — a wrong answer dressed as an answer. `--print` never offers the picker
  (it is non-interactive, and printing several hundred private repo names to stdout would be
  a disclosure), and its refusal now names BOTH causes: the flag AND the mapping's state,
  because only the second is actionable. `--print` still prints every candidate for real
  ambiguity. Nothing in the repo consumes `--print`.

## How to verify
```bash
# The 2026-09-03 resolver work, on the deployed artifact (not a checkout)
python3 ~/workspace/devrc/scripts/mention-open.py --print 'talos-infra#1065'   # civitai/talos-infra
# 🔴 Since #1328 there is no namesake search, so an unresolvable name REFUSES (exit 1) and
# names BOTH causes — the flag, and the mapping's state — rather than printing candidates.
python3 ~/workspace/devrc/scripts/mention-open.py --print 'kubernetes#1'       # exit 1, named reason
python3 ~/workspace/devrc/scripts/mention-open.py --print 'zzz-no-such-repo#1' # exit 1, named reason
# The six-digit branch: a colour literal is answered, not asked about (exit 1, named toast).
python3 ~/workspace/devrc/scripts/mention-open.py '#282828'

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
