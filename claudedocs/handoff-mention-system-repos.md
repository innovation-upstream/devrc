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
**The mention effort is COMPLETE and SHIPPED — eleven PRs merged.** `#1291` resolver ·
`#1313` detection+attribution · `#1322` copy-on-select · `#1328` local-first click ·
`#1336` KEYS guard + `audit-pr N` · `#1331` handoff · **`#1369` (`156b4927`) picker
universe + daily timer** · **`#1380` (`bc9b900a`) a guessed repo is overridable** ·
**`#1387` (`72e59dff`) bare `#N` too** · **`#1421` (`a6a1c6f3`) fzf RANKING** ·
**`#1426` (`3d02eea6`) `/pull` URLs** · **`#1454` (`e62d58ba`) fzf REPLACES rofi**.
Adjacent, merged the same arc: `#1403` (`4f49f5dc`) three false toolchain claims ·
`#1439` (`605b29ac`) ship.sh nebula fallback · `#1453` (`eeea9025`) co-tenant flake
root cause · `#1441` (`0972d1d6`) its diagnostic wiring · `#1457` (`0fb5b6b1`)
coverage guards.

**IN FLIGHT: `#1509`** — plausibility ordering (Tier A ranges + Tier B picks). Open,
NOT merged, NOT shipped. See rank 1.

**Live and verified on both hosts** (`ship.sh` converged + COMPARED; deployed wrapper
carries `alacritty-0.17.0` + `fzf-0.74.3`). Measured through the deployed wrapper,
headless, no window raised:

    typing `civitai`  -> civitai/civitai      (was 8th of 230)
    typing `devrc`    -> innovation-upstream/devrc
    typing `Civitai`  -> civitai/civitai      (the round-1 case regression, fixed)

🔴 **`main` IS RED and it is nobody's in this arc.** Two tests, reproduced by me on a
pristine `origin/main` export with none of #1509 present:
`test_guard_core.py::test_every_kill_server_call_site_in_the_repo_is_classified` and
`::test_no_tracked_shell_text_writes_a_kill_this_guard_would_deny` — 2 failed / 1534
passed. `claudedocs/handoff-tmux-webapp.md` landed carrying `tmux kill-server` text
without an entry in `_KILL_MENTION_LEDGER`. NEEDS AN OWNER.

**This session resolved no clawgate task** — `resolve` exited 5 with its positive
control passing (8 links for another session). 🔴 NOT evidence this session touched no
task: a wrong session id also answers 200 with an empty array.

## Open investigations — live diagnosis state
(none — the disclosure is a known, measured state awaiting an operator decision, not a
diagnosis in progress.)

## Next steps (ranked)
1. **Land or close `#1509`** (plausibility ordering). Open, gated (22,322 passed, the
   2 inherited failures above), claim released, 0 behind at its last gate. Decide, then
   `ship.sh`. ⚠ Its author DELETED a six-round concurrency ladder after measuring the
   lock unnecessary (−513 lines); do not re-add one without a measurement.
   forcing: none
2. **Classify `claudedocs/handoff-tmux-webapp.md` in `_KILL_MENTION_LEDGER`** — `main`
   is red until someone does. Two tests, reproducible on a clean export.
   forcing: gate — `claude/RULES.md` calls a permanently-red gate worse than none.
3. **Fix `test_kills_the_LENGTH_guard`** (`scripts/tests/test_subsystem_touch.py`) — a
   **0.195%** flake on `main` since 2026-08-12 (#432). A 3-hex sha prefix (4096 values)
   is sometimes ambiguous among the fixture repo's 9 objects; the mutant then raises
   `CommitAmbiguousError` and the assertion is never reached. 3 chars is deliberate
   (`COMMIT_SHA_MIN_CHARS = 4`), so "use more characters" is unavailable — regenerate
   until unambiguous, or assert the precondition. Closing condition: the 600-iteration
   loop returns 0 failures.
   forcing: gate — it reddens CI intermittently and trains everyone to click through.
4. ~~Exercise the Alacritty click path~~ — **DONE by the operator**, and it produced two
   real defects (the `audit-pr` one-row picker, the civitai ranking). Residual: the
   operator has not re-clicked since `#1454` swapped rofi for fzf.
   forcing: none
5. ~~Everything else from the old ranked list~~ — ranks 2-10 all **DONE**: staleness
   signal repurposed as the timer's deadman (#1328/#1369), clipboard WIP (#1322),
   detection/attribution (#1313), README (#1321), round-2 guard gaps (#1336), the four
   🟢 (#1387/#1457), picker universe (#1369), disclosure threshold now structural and
   19/20 → 9/20 (#1457).
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

- 🔴 **THE MERGED-TREE GATE CAUGHT A RED `main` NEITHER BRANCH COULD SEE.** `origin/main`
  collected **12818** and the PR head **12806** — both under the 12836 `scripts/tests` drift
  ceiling — while the MERGE collected **12843** and crossed it. Both tiers agreed; 12843
  passed, 0 failed. This is the same shape as the 2026-08 case recorded beside the floor
  entry (`alone (10137 and 10112); the SUM crossed it`), one merge later. **Neither side is
  over alone, so only a merged-tree gate can see it.** Fixed by raising the floor to
  `"scripts/tests|12793"` — **the number the gate printed itself**, and pinned AFTER merging
  `main` into the branch, per the ORDER note beside it (pinning first lands the branch under
  its own new floor and trades a merge-time failure for a branch-time one).
- 🔴 **`nix build path:<a worktree>` DEFEATS THE SANDBOX'S GIT-CONFIG ISOLATION.** MEASURED:
  the sandbox pytest leg failed `RESULT: FAIL (exit=2)` on
  `run-tests: FATAL — 'git config --global' does NOT write to this run's isolated file`,
  **before any test ran**, and it reproduced. Cause was the METHOD, not the tree: `path:`
  copies the worktree's `.git`, which is a **FILE** pointing at the real git dir, not a
  directory. The identical commit content extracted with `git archive` (no `.git`) passes.
  The rules document this hazard for `cp -a` copies of worktrees; **`nix build path:` is a
  second door to it.** Gate a worktree by extracting it first.
- 🔴 **AN "0 HITS OVER 220 COMBINATIONS" MEASUREMENT SUPPORTED A FALSE CONCLUSION.** A round-2
  audit called the `refuse()` staleness arm dead code, citing 220 mapping-state × text × flag
  combinations producing 110 refusals and **0 branch hits**, and deleting it left the suite
  green. It was relayed here as fact and was WRONG. The branch is reachable: `discover_repos`
  **overwrites**, and `parse_owner_repo` is looser than `OWNER_REPO_VALUE_RE`, so a checkout
  whose directory name shadows a mapping key writes an invalid row. Independently reproduced
  with a real `git init` + real remote, and the realistic case is **sourcehut's own
  `~user/repo` form** — `https://git.sr.ht/~sircmpwn/aerc` → `~sircmpwn/aerc`; six of seven
  remote URL forms tested reach it. **A sweep measures the shapes it CONSTRUCTS**; zero hits
  cannot distinguish unreachable from not-constructed.
- 🔴 **A DISCLOSURE GUARD WAS BLIND TO THE OWNER HALF — THE HALF THAT NAMES THE CLIENT.**
  `session-tailer.py`'s spool guard covered keys and full `owner/repo`, not bare owners.
  Measured: a keys-leak mutant **KILLED**, an owners-leak mutant **SURVIVED** with the suite
  reporting clean, and a positive control confirmed the owner names really reached the spool.
  Its sink is a **durable ClickHouse table**. Of the 232 names in the #1283 disclosure, 167
  were a client's — client identity IS the owner half. Fixed with a derived four-spelling
  ledger; K43 re-run at BOTH trees shows SURVIVED → KILLED.
- 🔴 **THE MUTATION INSTRUMENT HAD THREE SEPARATE DEFECTS, ALL FOUND THIS EFFORT.** (a) a
  mutant that made the handler UNIMPORTABLE scored SURVIVED, because the sanity floor ran
  only on the CONTROL; (b) the failure count came from the first `N failed` match anywhere,
  and the tailer's own `emitted=1 failed=0` output contains `1 failed` — so `emitted=0
  failed=0` would read `nfail=0` over a RED suite and score SURVIVED for a mutant every guard
  caught; (c) rows scored `KILLED-WRONG-REASON` because an earlier assertion fired with a
  message naming no hazard. **Every mutation result this subsystem produced rested on that
  instrument.** All three fixed; the battery is committed and re-runnable.
- 🔴 **A FIXTURE WHOSE KEYS ARE SUBSTRINGS OF ITS VALUES MAKES A VALUES-ONLY GUARD LOOK
  TOTAL.** `FAKE_UNIVERSE` and `FAKE_REPOS` both spelled every key inside its own value, so
  `.values()` guards appeared to cover keys. Both now pairwise-distinct and pinned.
  ⚠ And a ≥3-element fixture cannot kill a transposition mutant — a 3-element reversal leaves
  the middle element mapped to itself. Four has no fixed point.
- 🔴 **NINE tests were reading the operator's REAL `known_repos.json`** (7 `stat`,
  2 `read_text`, measured with an instrumented `Path`) — not the two first reported. Closed
  with an autouse redirect; ⚠ its first version was **in-process only**, so a subprocess
  walked straight through and the control could not see it, because it inspected a module
  attribute the child never consults. Fixed with `monkeypatch.setenv`.
- **`--print` CANNOT VERIFY PICKER-VS-OPEN.** It prints candidates and never shows a picker,
  so a single URL is consistent with BOTH auto-open and a one-row picker. Any claim about
  that distinction needs the interactive path, which raises a window.

- 🔴 **A DISPLAY LIST READ OUT OF A LOOKUP TABLE INHERITS THE LOOKUP'S FILTERS — AND THAT
  IS THE WHOLE RANK-9 BUG.** `repo_universe()` was `sorted(discover_repos().values())`, so
  the fuzzy picker silently obeyed both filters the RESOLUTION mapping needs: drop a repo
  whose issues are disabled, drop a bare name two owners share. Both are correct for "what
  does `foo#12` mean?" and wrong for "which repo do you want?", where the row is a full
  `owner/repo` and nothing opens without a selection. MEASURED: 388 repos on the host, 339
  offered — **53 unreachable by typing at the picker**. The two questions now have two
  corpora (`known_repos.json`, `known_universe.json`). Generalises past this feature: when
  a picker and a resolver share a source, ask which one's filters you inherited.
- 🔴 **THE `has_issues` FILTER'S STATED JUSTIFICATION WAS FALSE, AND MY FIRST PROBE
  CONFIRMED THE FALSE VERSION.** The comment said a bare `repo#N` "404s for EVERY N" in an
  issues-disabled repo. It does not for PULL REQUESTS. First probe: 4 issues-disabled repos
  via unauthenticated `curl`, 3 × 404 — which reads as confirmation and is **confounded**,
  because an unauthenticated request 404s on a PRIVATE repo whatever the redirect does.
  Re-run authenticated, with a positive control (2 issues-ENABLED repos first, both `PULL`):
  **6 of 6** resolved `/issues/<pr>` to the pull request. **The confound was invisible until
  the control was added** — the 3/4 split looked like a real mixed result. Ask what else
  produces your 404 before believing it.
- 🔴 **`user/repos` IS ALREADY "EVERY REPO I CONTRIBUTE TO" — MEASURED, SO NOBODY RE-ADDS A
  SOURCE.** Its default affiliation (`owner,collaborator,organization_member`) is the widest
  the endpoint offers. The plausible gap — a repo contributed to by PR without membership —
  was measured: `search/issues?q=author:<login> type:pr` returned **28** repos, **all 28**
  already in `user/repos`, **0 new**. The request was "all repos I contribute to"; the
  answer was not a new API source, it was deleting two filters.
- 🔴 **AN OBJECTION RECORDED IN CODE MUST BE ANSWERED, NOT STEPPED OVER.** The note beside
  `STALE_MAPPING_DAYS` had explicitly weighed and REJECTED this timer: a scheduled `gh api`
  run fails forever on a host without `gh auth`, so the unit is permanently red and toasts
  daily, and a permanently-red timer is worse than no timer. That was CORRECT against a
  generator with one failure code. Fix: exit **4** = not configured here (`SuccessExitStatus=4`,
  quiet), exit **3** = configured and broken (still toasts). Readiness comes from
  `gh auth status`'s **exit code**, never from matching words in its stderr — a phrasing
  change would otherwise report every host as configured. Verified live: rc 4 on a PATH
  with no `gh`, and the RENDERED unit inspected for `SuccessExitStatus=4` rather than
  trusting that it built.
- 🔴 **A NEW ARTEFACT SHAPE IS A NEW BLIND SPOT IN THE DISCLOSURE GUARD.**
  `test_the_generated_mapping_is_NOT_published_anywhere_in_this_repo` counts
  `key: "owner/repo"` PAIRS. `known_universe.json` is a JSON **list** — no pairs — so it
  scored **0** and would have sailed straight through the guard that exists to stop exactly
  this, while naming MORE private repos than the mapping (it is unfiltered). Same structural
  blindness as the #1283 incident, in a new shape. Added a detector for the list shape.
- 🔴 **AND THE FIRST VERSION OF THAT DETECTOR WAS TEXTUAL AND USELESS — 9 FALSE ACCUSATIONS
  ON ITS FIRST RUN.** Counting every quoted `a/b` flagged a `package-lock.json` (46, all npm
  `node_modules/…` paths) and eight test files whose fixtures merely mention repos. Replaced
  with a STRUCTURAL question — is this file a LIST of repositories? — parsed as JSON and via
  `ast` for the Python-literal spelling. **A guard that fires on ordinary files is a guard
  everyone learns to override**, which is worse than the gap it closes.
- 🔴 **A DISCLOSURE GUARD WAS ONE FIXTURE KEY FROM RED.** Its own test file carried **19**
  distinct keys against a threshold of **20**; a single new key tripped it. Worked around by
  reusing an existing key — see rank 10 for the structural fix, deliberately left out of the
  feature PR because narrowing a disclosure guard as a side effect is how #1283 happened.
- 🔴 **A TEST FIXTURE THAT INVENTS CONTENT IS NOT A REDIRECT — IT SILENTLY REWROTE NINE
  TESTS.** The autouse fixture that points the mapping away from the operator's real file
  was extended to the new universe file, and it initially WROTE default content there.
  Nine existing tests built to assert an EMPTY universe (unreadable mapping, mapping with no
  usable rows) then got a picker from the file instead of the refusal they pin, and failed
  for reasons unrelated to what they test. **A redirect must relocate a read, never supply
  content the test did not ask for.** The universe path is now redirected to a file that
  does not exist; tests wanting a universe write one.
- 🔴 **A BASE-VS-HEAD MATRIX CAN BE STRUCTURALLY IMPOSSIBLE, AND SAYING SO IS THE HONEST
  ANSWER.** The new `test_mention_open.py` cannot run at `origin/main` at all: its autouse
  fixture does `setattr(MO, "KNOWN_UNIVERSE_PATH", …)` and the old module has no such
  attribute, so all **163** tests ERROR at fixture setup. That is red-for-the-wrong-reason
  and proves nothing about any guard. Evidence for that half is a MUTATION BATTERY instead —
  8 mutants, each required to be killed by its OWN named test, plus a harness control that
  must SURVIVE. The generator half DID produce a real matrix (14 red at `969f0581`, 46 green
  at HEAD). **Report which half you got; an ERROR at base is not a FAIL at base.**
- **The `| tail` trap fired again, exactly as documented.** `gate.sh … | tail -30` printed
  `GATE_RC=0` over `GATE: RESULT=FAIL exit=1`. Read the runner's own verdict line.
- **`gate.sh` exit 3 in a worktree is a MISSING ENVIRONMENT, not a code failure** — the
  worktree's copied `.envrc` is `use opencode`, which puts no pytest on PATH. The gate says
  so itself and prints the `nix develop <worktree> --command …` to re-run. Not a red gate.
- **zsh does not word-split, and it bit a `for` loop over `find` output** (`for f in $S`
  ran once on the whole newline-joined string). Documented in CLAUDE.md; still hit it.

- 🔴 **A GREP THAT MATCHES ITS OWN TOOLING IS A FALSE ALARM WAITING TO HAPPEN, AND I
  SHIPPED ONE INTO THIS DOC'S OWN VERIFY BLOCK.** `git ls-files | grep -c 'known_repos'`
  was written with `# must be 0`; it returns **1** on a perfectly clean tree, because
  `scripts/tests/test_regen_known_repos.py` contains the string. Caught by reading WHAT
  matched instead of the count — the same discipline the rules demand for any tool
  verdict. Two failure modes, and the second is worse: a reader either chases a phantom
  disclosure, or learns that this particular check "always says 1" and stops reading it.
  **Ask for the ARTIFACT (`(known_repos|known_universe)\.json$`), and prefer the
  content-based test over any name-based grep.**
- 🔴 **A HOST-SPECIFIC COUNT IS NOT DRIFT — SAY WHICH IT IS BEFORE SOMEONE "FIXES" IT.**
  The universe is 392 rows on the workbench and 391 on the laptop. That is correct: it
  unions LOCAL CHECKOUTS, which differ per host, so the file is a per-host MEASUREMENT and
  not a replicated artifact. Both report the same 52 picker-only repos. A future reader
  comparing the two numbers and concluding a host is stale would be wrong.
- **A timer being SCHEDULED is not a timer that WORKS.** `list-timers` showing
  `active/waiting` says systemd parsed the unit, nothing more — it does not exercise the
  unit's PATH, its `HOME`, or whether `gh` is reachable from inside it. Both hosts were
  verified by `systemctl --user start` and reading `Result=success ExecMainStatus=0` plus
  the unit's own journal line, which is the only thing that tests the environment the
  timer will actually run in.

- 🔴 **A HANDFUL OF GREEN RUNS IS NOT EVIDENCE AGAINST A RARE FLAKE — FOUR TIMES IN ONE
  SESSION.** (1) The 2026-09-06 `gc --auto` refutation: 80 iterations against a ~1.5%
  event, ~70% chance of zero hits — *but see the retraction below, the refutation was
  sound for a different reason*. (2) An agent's "3/3 passed on both trees" for a 0.195%
  flake. (3) MY OWN claim that green CI on two sibling PRs "ruled out" that flake — at
  0.195%, two greens are expected **99.6%** of the time. (4) An isolation control run
  while the box was at load 58, which reds BOTH trees and reads as "pre-existing".
  🔴 **The fix is arithmetic, not care: size N to the rate you are testing for, and
  state the power.** An auditor declined a 3-run experiment on exactly this ground
  (~0.6% power) and measured 600 instead — that is what found the defect.
- 🔴 **AN ISOLATION CONTROL RUN UNDER LOAD DOES NOT ISOLATE FROM LOAD.** Both trees go
  red and it reads as "inherited". This produced a false "red on main" claim about a
  SIGTERM/SIGINT timing test that passes 3/3 on a quiet box. The discriminator is a
  quiet box, not a second tree.
- 🔴 **THE `gc --auto` REFUTATION WAS SOUND — THE RETRACTION OF THE RETRACTION.** An
  agent first called it "unsound on statistical power", then retracted: `git maintenance
  run --auto` is a DIFFERENT CODE PATH from what the gc probe tested. With 3 loose
  objects against a 6700 threshold `gc --auto` genuinely cannot fire. The refutation
  answered a NARROWER question than everyone thought was open. **An eliminated
  hypothesis was read as a closed question** — that is the lesson, not "beware weak
  probes".
- 🔴 **ROOT CAUSE OF THE CO-TENANT FLAKE — `git commit` SPAWNS `git maintenance run
  --auto --quiet --detach`, AND THE CHILD DETACHES FIRST, DECIDES IT HAS NO WORK
  SECOND.** So `gc.auto`'s loose-object threshold governs whether gc does WORK, never
  whether the PROCESS EXISTS; the fixture planted its own co-tenant. Verified
  independently with `GIT_TRACE2_EVENT` on a throwaway repo at 0 loose objects:
  `category=maintenance label='detach'`, suppressed by `-c maintenance.auto=false`.
  Fixed in #1453; the DIAGNOSTIC that could have found it three days earlier was wired
  into only ONE site of a six-site family (#1441 closed that).
- 🔴 **A CLEAN `git merge` IS NOT A CLEAN MERGE — MEASURED ON THIS ARC'S OWN PRs.**
  #1454 and #1457 conflicted textually in two files; resolving the text was trivial and
  the SEMANTIC conflict was the real one. Every sink guard #1454 added used
  `_no_universe_token_anywhere` ALONE — and that guard knows `FAKE_UNIVERSE`, which
  deliberately EXCLUDES the pane guess, which is exactly the blindness #1457 existed to
  close. A textually clean merge would have re-created that blindness in the NEW
  surfaces #1454 introduces (terminal argv, FIFO header, toast bodies) **with the whole
  suite green.**
- 🔴 **`--tiebreak=end` ALREADY IS `end,index` — fzf appends `index` IMPLICITLY.** I
  instructed an agent to add `,index` as "load-bearing", claiming the computed order
  would be "discarded the moment the operator types". FALSE. It measured 560 typed
  trials, **0 differences**, positive control (`length`) differing in 518, and declined
  to churn a whole-string-pinned constant for zero behaviour change. Independently
  confirmed. **Pre-ranking DOES survive a keystroke.**
- 🔴 **ROFI WAS NOT A WEAK IMPLEMENTATION — fzf's OWN default tiebreak is `length`.**
  rofi was faithfully reproducing upstream fzf. The fix was choosing a different
  tiebreak (`end` — in `…/owner/repo/pull/<id>` the suffix is constant, so "nearest the
  end" means "in the repo name rather than the owner"), not a better library. rofi
  simply cannot express it: no `--tiebreak`, `-display-columns` is DISPLAY-only
  (matching still runs over the whole row, verified headless), and reordering the row
  changes nothing (three shapes, all rank 8).
- 🔴 **A TERMINAL DOES NOT PROXY STDIN — it hands its child a PTY.** That is why the
  fzf picker uses a FIFO PAIR: argv is world-readable via `/proc`, env vars likewise, a
  temp file lands private repo names on disk. A FIFO holds nothing at rest. The header
  rides the same pipe as `--header-lines` rather than on argv, making "nothing carrying
  a universe token is ever an argument" STRUCTURAL. ⚠ A related defect was caught in
  #1509's own round 1: **50 private repo names were riding the `gh` child's argv.**
- 🔴 **A GUARD'S POSITIVE CONTROL THAT RE-IMPLEMENTS THE GUARD CERTIFIES A COPY.**
  #1454 round 3: the control built `redirs` with its own inline copy of the guard's
  regex. MEASURED by me — narrowing the real guard straight back to the round-2 defect
  left **232 passed**, including the control written to prevent exactly that. Drive
  guard and control through ONE function. (This is the SECOND recorded instance in this
  repo; the first walked `ast.Attribute` while the guard walked `Attribute` and `Name`.)
- 🔴 **`/bin/sh` APPLIES REDIRECTIONS BEFORE COMMAND LOOKUP.** `sh -c 'missing <"$1"
  >"$2"'` exits 127 **with the FIFOs opened**, so a missing `fzf` gives the rows FIFO a
  reader and the run ends as a SILENT dismissal — not the `never-shown` arm. A toast
  that named "check alacritty and fzf are on the PATH" could be reached by NEITHER of
  those causes. Fixed with a `shutil.which` pre-flight BEFORE the spawn, so no window is
  raised for a picker that cannot run. ⚠ `proc.returncode` is NOT a remedy — alacritty
  0.17.0 exits **0** whether its `-e` command exits 127 or 0 (but **1** if the `-e`
  binary itself does not exist).
- 🔴 **A PICKER'S `-i` IS LOAD-BEARING AND ITS LOSS IS SILENT.** rofi forced
  case-insensitive; fzf defaults to SMART CASE, so `Civitai` returned **0 rows** — and
  an empty list is indistinguishable from a dismissal, which is the exact silent wall
  the subsystem exists to remove. No test caught it: the old test pinned `-matching
  fuzzy` and `-no-custom`, never `-i`.
- 🔴 **A DISPLAY LIST READ OUT OF A LOOKUP TABLE INHERITS THE LOOKUP'S FILTERS.**
  `repo_universe()` read the RESOLUTION mapping's `.values()`, so the picker silently
  obeyed filters that mapping needs and a picker does not. MEASURED: 388 repos, 339
  offered, **53 unreachable**. And the `has_issues` filter's stated reason ("404s for
  EVERY N") is FALSE for pull requests — 6/6 issues-disabled repos resolved
  `/issues/<pr>` to the PR, with a positive control. ⚠ My FIRST probe (unauthenticated
  `curl`, 3 of 4 → 404) *confirmed the false version*: an unauthenticated request 404s
  on a PRIVATE repo whatever the redirect does.
- 🔴 **`user/repos` IS ALREADY "EVERY REPO I CONTRIBUTE TO" — MEASURED, so nobody adds
  a second source.** `search/issues?q=author:<login> type:pr` returned **28** repos,
  **all 28** already in `user/repos`, **0 new**.
- **BOTH GitHub URL forms normalise in BOTH directions** — `/issues/<pr>` → `/pull/<pr>`
  AND `/pull/<issue>` → `/issues/<issue>`. So neither is safer and the choice is what
  the operator READS. ⚠ A blind find-replace would have been GREEN AND WRONG: the
  scanner matches `(?:pull|issues)` on INPUT, so rewriting every `/issues/` in the tests
  would have converted the one input fixture and **silently deleted coverage of that
  form**. Nothing fails when coverage disappears.
- 🔴 **`ship.sh` IS LAN-ONLY FOR THE LAPTOP UNTIL #1439 — and it bit twice.** rc 255
  with **one host silently converged** and `cross-host agreement NOT COMPARED`. The
  laptop answers on nebula (`zach@10.42.0.100`) the whole time. Also seen: **rc 19**,
  main moving BETWEEN the two legs' fetches so each host landed on a different sha with
  every per-host line green — read the final verdict, not the greens.
- 🔴 **THE REPO AUTO-DELETES BRANCHES, SO A DOCUMENTED RULE CANNOT DO WHAT IT CLAIMS.**
  `delete_branch_on_merge=true` (verified). So *"never `--delete-branch` a stacked
  parent"* **cannot protect a stacked child here** — the setting overrides the flag. A
  stacked child must be RETARGETED before the parent merges.
- **Harness traps measured this session:** creating a new background task **EVICTS the
  oldest** (`RESULT: FAIL (exit=143)`, zero FAILED lines — reads as an unexplained
  failure; `setsid` avoids it). `ls -t /tmp/nix-shell.*/devrc-gate-*/pytest.log` reaches
  **sibling agents' gates** — one agent took another's red for its own. `nix build
  --rebuild` does NOT re-run a failed derivation and `nix log` then serves the OLD log.
- ⚠ **CARRIED FORWARD from the old ranked list, because a REPLACE heading would have
  eaten the measurement.** (a) **Tekton's `devrc-pytests` leg has a history of reddening
  on tests a PR cannot reach** — MEASURED 2026-09-05: red on
  `test_mjs_parses[attachments.mjs]` for `#1328` **and** for `#1326`, which is a single
  markdown file and cannot reach `.mjs` parsing, while the same test passed on clean
  `main`, on the PR head, and in the local sandbox tier. Re-measured 2026-09-09 across
  this arc: pass / fail / fail / pass, so it is INTERMITTENT rather than permanently
  red, and the systematic half was #1392's toolchain drift. Do not read a single red
  Tekton leg as a verdict on your diff. (b) **The disclosure guard in
  `test_regen_known_repos.py` sat at 19 distinct fixture keys against its own threshold
  of 20** before #1457 made it structural (now 9/20) — one added fixture key would have
  reddened a DISCLOSURE guard, and the fixes that come to hand under pressure (raise the
  threshold, exclude the file) gut it.

## How to verify
```bash
# The click path, on the deployed artifact. NOTE: mention-open runs from the WORKING
# TREE, but the PICKER now needs a nix-store wrapper carrying fzf+alacritty, so a
# `git pull` alone is NOT enough since #1454 — it needs a `home-manager switch`.
python3 ~/workspace/devrc/scripts/mention-open.py --print 'civitai/talos-infra#1065'  # /pull/ URL
python3 ~/workspace/devrc/scripts/mention-open.py --print '#1291'                     # clawgate first
python3 ~/workspace/devrc/scripts/mention-open.py --print '#282828'                   # colour reason

# The wrapper actually carries the picker's binaries
W=$(grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' ~/.config/alacritty/alacritty.toml | head -1)
grep -oE "(alacritty|fzf)-[0-9.]+" "$W"      # expect alacritty-0.17.0 and fzf-0.74.3

# Ranking, headless — NEVER raise a window
fzf -i --tiebreak=end --filter=civitai < <rows>   # civitai/civitai must be first

# The generated per-host files: counts only, NEVER paste the rows
ls -l ~/.config/mention-open/            # known_repos.json, known_universe.json (+ ranges/picks after #1509)
git -C ~/workspace/devrc ls-files | grep -E '(known_repos|known_universe|known_ranges|picks)\.jsonl?$'   # must print NOTHING

# Both hosts
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@10.42.0.100 'git -C ~/workspace/devrc rev-parse --short HEAD'   # nebula; LAN is often down
```
