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
**The whole mention effort is SHIPPED.** Four PRs merged and deployed to both hosts:
`#1291` (`fd68d48c`) resolver · `#1313` (`3c324156`) detection widening + attribution ·
`#1322` (`a75ffc6a`) alacritty copy-on-select · `#1328` (`3f4d9c0f`) local-first click path.
Both hosts converged and **compared** at `3f4d9c0f`; `ship.sh` reports
`NO dirty path is read by nix`, so the artifacts genuinely match — the host divergence this
doc used to record is CLOSED.

**Operator click-test 2026-09-05 — the first end-to-end evidence this feature ever had.**
`talos-infra#1065` opened correctly. Two defects came out of it, both now fixed:
- `dashboard#12` took ~10 s. **Not a performance problem — a lookup-order one.** The click path
  searched ALL of GitHub before ever consulting the operator's own 369-repo mapping, for a name
  the mapping does not hold. Measured 4.26 s at 6% CPU (pure network wait), returning only
  strangers' repos. **Fixed in #1328 by deleting the GitHub-wide search**: the candidate
  universe is now the local allowlist only. **Measured on the deployed artifact: 4.26 s →
  0.086 s.**
- A bare `#N` appeared broken. **It was not** — `#1291`/`#370`/`#12` all resolved. The operator
  had clicked `#282828`, the gruvbox background literal, which the six-digit guard correctly
  rejects. The message (`no mention in the clicked text`) made correct behaviour read as a bug;
  it now names the reason.

⚠ **The click path has changed materially since that test** (local-first, picker-on-failure,
six-digit toast) — so ranked item 1 is worth ONE more pass, and it is still the only
end-to-end evidence nobody can produce but the operator.

🔴 **Tekton's `devrc-pytests` check is RED for reasons unrelated to any diff, and #1328 was
merged past it.** It fails on `test_mjs_parses[attachments.mjs]`. Evidence it is inherited:
the same leg is red on **#1326, which is a single markdown file** and cannot reach `.mjs`
parsing; and the test passes on clean `main`, on the PR branch head, AND in the local nix
sandbox tier — three environments. Per this repo's own rule a permanently-red gate trains
everyone to click through, which is exactly what happened here. **Someone should fix or
retire that leg**; it is not this effort's to own, and it is not evidence about this work.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. **Exercise the Alacritty click path once more** — the code changed after the 2026-09-05
   test. Plain left-click (`mouse.enabled = true`, no mods; `Ctrl+Shift+M` for the keyboard
   route). `talos-infra#1065` → opens. `dashboard#12` → picker in ~80 ms, from YOUR repos.
   `#282828` → named toast, no window raised. A bare `#N` with no resolvable pane repo →
   picker with the clawgate task first.
   🔴 NEEDS A HUMAN — clicking raises windows, a `pkill`-class action for an agent.
   forcing: none
2. ~~Staleness signal for `known_repos.json`~~ — **DONE in #1328**, and deliberately a SIGNAL,
   not a timer: `regen-known-repos.py` exits 3 below its 25-repo floor, so a systemd timer
   would be a permanently-red unit and a failure toast on any host without `gh auth`.
   `mapping_age_days()` feeds the picker note and a `--print` refusal note past
   `STALE_MAPPING_DAYS = 7`; `None` (absent) is never treated as fresh. Kept numbered so
   ranks do not re-point.
   forcing: none
3. ~~Commit the workbench's `save_to_clipboard` WIP~~ — **DONE in #1322** (`a75ffc6a`).
   🔴 This item was STALE for hours and told the next session to redo merged work — a
   round-2 audit caught it. The "latent rc 7 for the next ship that touches this file"
   warning was false, and pointed at the very PR that had already fixed it.
   forcing: none
4. ~~Review, gate and merge the detection/attribution PR~~ — **DONE** (#1313).
   forcing: none
5. ~~Fix `scripts/collector/README.md`~~ — **DONE in #1321**, which also fixed two
   restatements of the retired invariant inside `session-tailer.py`.
   forcing: none
6. **Close the two guard gaps round 2 found** (devrc; `scripts/mention-open.py` +
   `scripts/tests/test_mention_open.py` + the battery). IN FLIGHT: see the open PR.
   (a) the staleness note is unreachable on the non-`--print` refusal path — measured 220
   combinations, 110 refusals, **0 branch hits**; (b) the disclosure tripwire iterates
   universe VALUES and never KEYS, so a "did you mean?" mutant leaking every private repo
   NAME survives 127/127.
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

- 🔴 **THE LOOKUP-ORDER LESSON, which generalises past this feature.** A 10-second click was
  not slow code: it asked a remote, unbounded corpus first and a local, authoritative one
  never. The fix was a DELETION. **When something is slow, ask what corpus it is searching
  and whether a smaller authoritative one was already on disk** — before profiling, caching or
  parallelising. Measured: 4.26 s → 0.086 s, and the deleted path could only ever return
  repos the operator does not own.
- 🔴 **"It's broken" and "it's correct but says so badly" are indistinguishable to the
  operator.** The bare-`#N` report was the guard working exactly as designed; the wording made
  it read as a failure, and it cost a full investigation to establish. **A refusal must name
  its reason** — `no mention in the clicked text` says nothing; `#282828 is six digits — that
  looks like a colour literal` ends the question.
- 🔴 **DELETING A PASS WAS NOT ENOUGH — the fan-out was the other 60%.** `discover_repos` ran
  **100 serial `git remote get-url` children (149-184 ms)**. Removing the network call alone
  would have landed at ~170 ms and quietly missed the target. Only measurement found it.
  Second-order: the fix's own top-level `import concurrent.futures` cost ~5 ms on EVERY click
  including ones that never discover — caught only by an **interleaved** A/B (A,B,A,B), where a
  plain before/after run had made it look like load. It is imported lazily now.
- 🔴 **A ≥3-element fixture is NOT enough to kill a transposition mutant** — a 3-element
  reversal leaves the middle element mapped to itself, so a third of the mutation is
  invisible. Use four; four has no fixed point. ⚠ And the corollary the round-2 audit added:
  the *reason* stated above is stronger than it needs to be — three would also have killed
  that particular mutant, because the assertion is whole-dict equality. Four is still right;
  the justification does not establish what it claims.
- 🔴 **Two tests were reading the operator's REAL `known_repos.json`**, so they asserted
  different things on the dev host and in the nix sandbox (empty `HOME`). A test that reads
  live host state is structurally blind in one tier — and these were DISCLOSURE tests.
- 🔴 **A guard can be one spelling short of the substitution its own docstring names.** The
  no-network ledger keyed `(argv[0], argv[1])` and so admitted `git remote update` — which
  runs `git fetch` per remote — while its docstring said the subcommand was in the key
  *because* bare `git` would admit `git fetch`. Now keyed on the verb path.
- 🔴 **The mutation instrument itself scored an UNIMPORTABLE handler as SURVIVED**, because its
  sanity floor ran only on the control, not per mutant. Every mutation result this subsystem
  produced rested on that. Fixed with a `NOT-OBSERVED` verdict arm.
- **`notify-send` swallows a body that begins with `--`** — it parses it as an option, exits 1,
  and shows NO toast. Both new refusal bodies started with `--`. Fix: `notify-send … -- SUMMARY
  BODY`. A refusal that displays nothing is worse than the one it replaced.
- **rofi cannot distinguish "typed a name, nothing matched, Escape" from "changed my mind"** —
  `-no-custom` makes both a non-zero exit with no selection. So the diagnosis goes ABOVE the
  list as `-mesg` rather than being toasted after every dismissal.

## How to verify
```bash
# The click path, on the deployed artifact (mention-open runs from the WORKING TREE,
# so `git pull` alone changes it — only the telemetry half needs a switch)
time python3 ~/workspace/devrc/scripts/mention-open.py --print 'dashboard#12'   # ~0.09s
python3 ~/workspace/devrc/scripts/mention-open.py --print 'talos-infra#1065'    # civitai/talos-infra
python3 ~/workspace/devrc/scripts/mention-open.py --print '#1291'               # clawgate task
python3 ~/workspace/devrc/scripts/mention-open.py --print '#282828'             # names the colour reason

# Which half needs a switch — readlink is the arbiter, never a diff
readlink -f ~/.config/alacritty/alacritty.toml            # /nix/store/... -> needs a switch
readlink -f ~/.config/activity-collector/mention_scan.py  # /nix/store/... -> needs a switch

# The mapping is per-host, untracked, 0600
ls -l ~/.config/mention-open/known_repos.json
git -C ~/workspace/devrc ls-files | grep -c 'collector/known_repos'   # must be 0

# The committed mutation battery (re-runnable, unlike round 1's)
nix develop ~/workspace/devrc -c python3 ~/workspace/devrc/scripts/tests/mutation_battery_mentions.py

# Both hosts on the same sha
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@192.168.50.155 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
