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
- 🔴 **THE CLICK PATH RAN — and the widget WORKS.** The operator reloaded on the laptop
  and reported back on real behaviour: the card renders, it collapses to a pill, and the
  other-accounts section is populated. That is the first time anything in this arc has
  been observed in a browser, and it retires the whole "never registered / never loaded"
  framing that dominated the last three docs.
- 🔴 **AND IT SURFACED TWO DEFECTS, WHICH ARE ONE MECHANISM.** Verbatim: *"the widget
  needs to live-refresh, currently it requires full page reload after switching accounts
  to update"* and *"cycled through a bunch of accounts while widget was minimized, the
  accounts usage/reset didn't update in the widget"*. Root cause: **nothing re-probes on
  an in-tab account switch.** The four triggers that refresh stored data are
  `autoRun` at `document_idle`, the SW's `onTabUpdated(status=complete)`,
  `onTabActivated`, and the 15-minute `cu-reprobe` alarm — an SPA account switch inside
  one tab fires none of the first three, so the numbers could only move on the alarm.
  Minimized was incidental (a collapsed widget paints only the pill).
- **PR #1835 OPEN** — `fix/claude-usage-live-refresh`, head `114205a1`, `MERGEABLE`,
  `mergeStateStatus: UNSTABLE` with all four Tekton checks **pending** at the time of
  writing. Adds a cheap active-org check to `content_probe.js`; manifest **0.3.0 →
  0.3.1**. NOT merged, NOT shipped, NOT observed in a browser.
- 🔴 **THE WIDGET WAS NEVER THE FAULT, and three rivals were ruled out by reading the
  code rather than assumed** — worth keeping, because the reported symptom points
  squarely at the widget and a session that starts there will burn hours:
  `content_widget.js` already repaints on a 30s tick AND on `chrome.storage.onChanged`,
  collapsed or not (so "collapsed skips repaint" is false); `handleReport` merges via
  `mergeStored` into the stored map (so "a probe overwrites the other accounts" is
  false); `PROBE_MIN_INTERVAL_MS` is **30s** and gates only the WORKER'S ASK, never the
  content script's auto-run (so "the cycle was rate-limited" is false).
- **MEASURED 2026-09-21, the fact that proved a reload was genuinely required** —
  laptop Brave's browser process started `2026-09-20 14:52:54`; the 0.3.0 files landed
  `2026-09-21 00:07:46`, **~9h later**. So the registered extension was running
  pre-0.3.0 code. This is the general instrument: compare `/proc/<brave-pid>` start time
  against the extension dir's newest mtime instead of guessing whether a reload is due.
- **Registration, re-measured on the laptop (positive-controlled against
  `browser-bridge-ext`, which returns the same two profiles):** `Default` →
  `onglbmcagkpoaapfeeoepblcbeanfanl` loading from
  `~/workspace/devrc/scripts/claude-usage/extension`; `Profile 1` →
  `doiabidngiihgfkpgdcmeccjohjgfmoe` loading from `~/.local/share/claude-usage-ext`.
  Both manifests on disk read 0.3.0. The **workbench is still unregistered in all
  profiles** (same positive control returns `Default` + `Profile 2`).
- **The two stores, measured — this is the `.local` split, in bytes:** Default/repo-path
  `3,268,444 B` with `12,456` `resetsAt` occurrences; Profile 1/`.local` **`811 B`,
  `0` `resetsAt`**. The repo-path registration is the one with all the account history.
- **Earlier arc, carried forward — do not let a status rewrite drop these two:**
  #1792 MERGED (`da695960`); #1806 MERGED (`7d323b48`) — the duplicate-notification fix;
  **#1803 CLOSED unmerged**, superseded by #1804, which fixed the same red the other
  way: **it pins opencode back to 1.18.29 via a frozen input, because 1.18.30 cannot run
  a prompt.** That pin is durable — do not "modernise" the input without re-running
  `test_opencode_engine.py`. #1801 MERGED (squash `52571758`); #1808 MERGED (squash
  `62bd4d9a`); #1817 MERGED (squash `4487d2dd`).
- **Audit ladder on #1817 is CLOSED at round 4** — a clean round ENDS the ladder and
  none was run to confirm it. Per-round detail is under `## Defects (batched)` and in
  the three `audit-claims` ISSUE comments on that PR. #1835 has had **no audit round at
  all** yet; its `## Defects` entry is self-review only.
- **Hosts:** both at `b84745f2`, one docs commit behind `origin/main` (`825b6f25`, an
  unrelated initiative). `~/.local/share/claude-usage-ext/manifest.json` present on the
  workbench at 0.3.0, so that clause of the closing condition is met.
- **Deploy/verify status, honestly:** #1817 is merged and shipped and its widget is
  observed working. #1835 is none of those things. The closing condition is **NOT met**;
  the unmet clauses are the badge/toast/popup half of the click path, and now the
  live-refresh fix on top of it.
- **This doc was landed from a worktree off PR #1834's branch, not off `origin/main`.**
  #1834 was open and rewrote `## State now` wholesale over the same file; basing on
  `origin/main` would have produced a guaranteed textual conflict and lost one session's
  findings. Worktree `/tmp/devrc-handoff-cu2`, branch `docs/handoff-cu-live-refresh`,
  **stacked on `docs/handoff-cu-availability-shipped`** — so #1834 must merge FIRST, or
  this PR retargeted.

## Next steps (ranked)
1. **Merge #1835, ship, reload, and run the click path AGAIN — the arc's closing
   condition, now with a second thing to check.** `scripts/ship.sh` both hosts → on the
   laptop `brave://extensions` → **Reload** *Claude Usage Tracker* in the **Default**
   profile (NOT `Profile 1`) → confirm the card reads **0.3.1** → open `claude.ai`.
   Then the two checks that are new: **switch accounts WITHOUT reloading the page** and
   confirm the card follows within ~10s, and confirm the still-unverified half of the
   original condition — **toolbar badge shows session %, the popup lists accounts, a
   threshold toast fires**. Capture the service-worker console on any misbehaviour
   BEFORE diagnosing.
   ⚠ Expect a **dimmed card with a saturated green or red dot** for a stale record. That
   is correct — it is what makes the card agree with the list.
   `forcing: user` — no test can prove an MV3 content script re-probes in a real SPA,
   and only a human can click Reload.
2. **Remove the `Profile 1` registration** pointing at `~/.local/share/claude-usage-ext`
   (`brave://extensions` in that profile), **and delete the `.local` load-path claim**
   from `scripts/claude-usage/extension/manifest.json`'s comment and
   `scripts/claude-usage/README.md` (still present — re-read 2026-09-21, README lines
   8–11). Two instances = two independent stores; the measurement is now in `State now`
   (3.2 MB / 12,456 `resetsAt` vs 811 B / 0).
   `forcing: user` — a live cause of the "only showing the currently logged in one"
   symptom the operator reported.
3. **`SCOPE: FULL` is printed on a run that did not complete.** MEASURED 2026-09-20 on
   the dev-host pytest tier: `SCOPE: FULL (30 of 30 hermetic target(s))` alongside
   `RESULT: FAIL (exit=143)` on a run that finished **21 of 30** targets (22,275 passed,
   0 failed; killed by `timeout --kill-after=30s 3600`). The `SCOPE:` line reports the
   INTENDED target set, not what finished. `gate.sh` exits **91 = PARTIAL** precisely
   when a tier does not report `SCOPE: FULL`, so a killed run that still claims it
   defeats that mechanism; only `RESULT:` distinguishes them. Fix in
   `scripts/run-tests.sh`.
   `forcing: gate` — it is a false-green surface in the gate's own stop mechanism.
4. Re-propose the probe-dedup cut from #1806 (one page load still costs
   `2x(/api/organizations + one /usage per org)`). ⚠ **Re-scope it first: #1835 already
   built the `runProbe(pre)` door** that lets a caller hand over an org list it already
   fetched, which is the same lever — the remaining waste is the auto-run/`onTabUpdated`
   double-fire, not the org fetch.
   `forcing: none`

## Defects (batched)
<!-- This heading REPLACES, it does not append — carry prior rounds forward. -->
- #1792 review round 1 (all fixed in `7e1bc585`): stale proposal status line;
  `weekly.lockedReason` unconsumed → weekly-lock alert added (the reviewer's premise
  that the API lacks the field was WRONG — `locked_reason` is on every window);
  `badgeFor(null, truthy-org)` TypeError; `pickActiveOrg` ran on the RAW org list;
  `normalizeUsage` docstring narrowed.
- Rounds 0–2 on #1801: `make-icons.sh` cut; badge/widget severity predicates
  consolidated into `lib/severity.js`; 🔴 the widget was **dead on arrival** —
  `lib/severity.js` missing from `web_accessible_resources`, the rejection swallowed by
  a catch; silent `import()` failure; frozen-forever card on a dead context;
  `pointer-events` over claude.ai's composer; `severityTone` returning `Object`'s
  constructor; the record tone vanished from the card; `retire()` force-expanded a
  collapsed widget; the manifest guard blind to dynamic imports.
- Round 0 on #1806: the whole diagnosis was fitted to a COUNT; `reportsSettled()`
  deleted as measured-redundant; the probe-dedup half reverted as unasked-for.
- **#1817, audit ladder rounds 0–4 — all fixed; full per-round detail is in the three
  `audit-claims` ISSUE comments on the PR.** Headlines: round 0 questioned the premise
  and found the popup already listed all accounts; round 1 found the availability
  verdict read the SESSION window alone, so a weekly-blocked account ranked first in
  green as the top switch target, and found the cross-surface seam still open on a
  `lastActiveOrg` naming no stored record; round 2 found a REGRESSION round 1's own fix
  introduced (`toneForRow` colouring a FREE row from a SPENT weekly reading) plus the
  unparseable-reset rule pinned for one window only; round 3 found the card's headline
  colour still painting red off spent weekly evidence — pre-existing code that new
  prose made read as closed; round 4 found the staleness dimension unpinned (a
  `stale ? "stale" : …` edit passed all 257 tests while silently re-opening the
  card-vs-row split).
- **#1835 self-review, before any audit ran (both caught by writing the test, not by
  running it):** the dead-context teardown test was **VACUOUS** — it asserted "0 timers"
  without installing a `document`, and `startWatchers()` returns early when there is no
  `document`, so it passed identically with the entire teardown deleted; and the
  fixture's `chrome.runtime` carried **no `id`**, which models a permanently-DEAD
  extension context and would have made the live half of that contrast unobservable.
  🔴 **#1834 appended two SUPERSEDED/RESOLVED investigation blocks but left the ORIGINAL
  headings live** at what are now lines 309 and 335 — the exact failure
  `handoff/reference/supersede.md` describes, where a reader meets the stale block first
  because appends go to the bottom. Retired in this delta.

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

- 🔴 **`gh pr merge` can exit 0 WITHOUT MERGING** — on #1803 it printed conflict
  instructions and returned success. Verify a squash merge by CONTENT
  (`state`, `mergedAt`, and the change present in `origin/main`), never by rc.
- 🔴 **A COUNT cannot distinguish two mechanisms, exactly like an empty result.**
  "2 notifications" was matched to a read-modify-write race that also produces
  two, and written up as "two IDENTICAL notifications" — a word the operator
  never used. Asking him what they SAID took one question and redirected the
  whole PR. The real defect: an alert and the summary both fire for one report.
- 🔴 **A guard written to close one blind spot walked into another, twice.** The
  `git ls-files` trackedness check went red in the nix sandbox (no `.git`) while
  green on every dev host. And the first regression test for round 2's finding
  was watched GREEN at the broken tip — it asserted on the MODEL while the
  defect was in the PAINTER.
- 🔴 **A fixture that cannot model an API the code uses produces a red that reads
  exactly like a real defect.** The shadow-DOM harness needed three extensions
  (`cssText` setter, `id` property, variadic `append`); each threw inside a
  swallowed catch, and the third's symptom was literally the regression under
  test.
- **node's ESM cache does not re-evaluate a module per query string** — a
  cache-busting `?t=` gives one instance, so every test after the first saw an
  empty page. `content_widget.js` grew a `NO_AUTOSTART` hook + `__CU_WIDGET__`
  surface like its two siblings.
- **A `--` inside an XML comment makes librsvg refuse the whole SVG.** Hit twice
  while writing the comment that warns about it; the regeneration command lives
  in `scripts/claude-usage/README.md` for that reason, not in `icon.svg`.
- **No `clawgate-task:` field is recorded**: `clawgate_handoff.sh resolve`
  exited 5 (nothing resolved). Its positive control proves the board is
  reachable but NOT that this session's id is right — a wrong id also answers
  200 with an empty array. Not a clean bill of health.

- 🔴 **The `.local` deploy was STALE AT 0.1.0 — loading from it would have registered
  the pre-widget extension and produced a "the widget is broken" that had nothing to
  do with the widget.** MEASURED 2026-09-20 after #1801 merged:
  `~/.local/share/claude-usage-ext/manifest.json` reads `version 0.1.0`,
  `content_scripts[0].js == ["content_probe.js"]` (no `content_widget.js`) and
  `web_accessible_resources == []`, while the repo path reads 0.2.0 with both. It is a
  `home.file`-class copy, so **`git pull` and a merge change it not at all** — only a
  `home-manager switch` does. This is the concrete cost of the open `.local`
  investigation and the reason the operator's choice of the base-clone repo path as
  the load path was the right one.
- **A merged PR's head can differ from the head the last handoff recorded.** #1801 was
  written up at `eda25023` and merged at `37f45c9d` — a merge commit taking
  `origin/main`. Re-read `headRefOid` before quoting any audit anchor or check result
  from a previous session's doc; a green recorded against the old head says nothing
  about the one that merged.
- **`getManifest()` describes the DIRECTORY, not the running code** — recalled from
  `claudedocs/archive/handoff-browser-bridge-emulate-and-staleness.md` and directly
  relevant to the click path: after loading unpacked, a version reading 0.2.0 proves
  the directory is right, NOT that Brave re-evaluated the code. Use observed widget
  behaviour, not a version string, as the evidence the load took.
- **The clawgate board has no task for this session.** `clawgate_handoff.sh resolve`
  exited **5** (nothing resolved), so no `clawgate-task:` field is recorded. Its
  positive control answered 11 links for a DIFFERENT session id, proving the board is
  reachable and the token accepted — but a wrong id also answers 200 with an empty
  array, so this is NOT a clean bill of health.
- **This doc was landed from a worktree, not the base clone.** `~/workspace/devrc` sits
  on `main` and `CLAUDE.md` forbids committing there in either host checkout, while
  `handoff_doc.py` commits wherever the checkout sits. Worktree
  `~/workspace/devrc-handoff-cu`, branch `docs/handoff-cu-widget-merged`.

- 🔴 **`error` is not `failure`, and it saved a false alarm here.** Tekton posted RED on
  all four legs of `6e5cdcef`; reading the raw statuses showed `state=error` with
  `NO CAPACITY: <leg> — the gate never started (queued past its deadline). Not a code
  failure.` `origin/main`'s own `devrc-main-*` checks were green throughout. **Read
  `state` and `description` from `/commits/<sha>/status`, never the bucket colour.**
- 🔴 **We starved our own CI.** This ladder ran local dev-host and sandbox tiers
  continuously for hours; the box hit load average 23–33 on 24 cores; Tekton then could
  not schedule a single leg. `NO CAPACITY` is documented as rare here (once in 80
  heads) and we took it on all four at once. The same contention killed the dev-host
  pytest tier at its 3600s cap (`scripts/tests` 296s → 2,276s, ~7.7× on the same tree).
  **For a small delta, run the change-scoped subset, not the full tiers.**
- 🔴 **A subagent's Monitor does NOT survive the agent stopping.** One fix agent ended
  with "both monitors are armed, waiting for the tier verdicts" — those verdicts were
  delivered to nobody and its report was lost. Its commits were safe; only the report
  died. Arm CI monitors in the parent session.
- 🔴 **`node --test <dir>` yields a bogus `tests 1`, and an unquoted `$FILES` in zsh is
  passed as ONE argument** — the second produced a run that printed NOTHING and greps
  as "no failures". **Assert the file count before believing a suite result**: a run
  that reported `files: 1` instead of 14 measured nothing, and was caught only by
  checking the count, not the verdict.
- 🔴 **A digest comparison that cannot fail is not an instrument.** A fix round ran a
  9,600-shape sweep across base and HEAD comparing every rendered string, got
  byte-identical digests, and offered that as proof a count could not have moved. The
  conclusion was right but the evidence was the DIFF (only two `tone:` expressions
  changed, no rendered string) — the digest match was equally consistent with "the sweep
  observed everything" and "the sweep observed nothing", because it carried no positive
  control. A tone digest, which MUST differ, would have made it one.
- 🔴 **A test-name filter that matches nothing reports `tests 1 / pass 1`** — identical
  to a real pass. One mutation result was scored SURVIVED that way (an `^…$` anchor on a
  truncated test name); a non-matching negative control exposed it. The tell is the
  runner printing the FILE PATH where a test name belongs.
- **An equivalent mutant is a legitimate outcome — delete the claim, do not invent a
  guard.** `orderForSwitch`'s `index` tiebreak was documented as what makes the order
  total; `return 1` produces byte-identical output at every tied run length 2→33 because
  V8 only reorders on a negative comparator. Independently re-derived over 4,709
  orderings, 0 diffs. The claim was deleted rather than guarded.
- **A PR's head can move under a handoff.** #1801 was written up at `eda25023` and
  merged at `37f45c9d` (a merge taking `origin/main`). Re-read `headRefOid` before
  quoting any audit anchor or check result from a previous session's doc.
- **`git merge-base` matters for attribution.** `main` ran 2–4 commits ahead of the PR's
  merge base throughout; a two-dot `main..head` diff shows unrelated main-side deletions
  (`scripts/stt`) as the PR's. Diff against the merge base.
- **The registration check needs a positive control, and its SCOPE is per-host.** A bare
  zero from `grep -l 'claude-usage' ~/.config/BraveSoftware/Brave-Browser/*/Preferences`
  is what let "the extension has never been registered" be stated as an arc-wide fact
  when it was true of the workbench and FALSE of the laptop, where it had been
  registered and recording all along. Pair it with `browser-bridge-ext`, and name the
  host in the claim.

- 🔴 **THE ISOLATION SEAM, in its textbook shape.** `content_widget.js` and
  `content_probe.js` were each correct, each hermetically tested, and the whole feature
  had survived a 5-round audit ladder plus a mutation sweep — and the defect lived in
  the RELATIONSHIP neither owned: *who re-runs the probe*. Every test was scoped to one
  surface, so none ever asked "what refreshes the data this painter reads?". The
  operator found it in 30 seconds of real use. **Ask which surface your fixture does not
  load.**
- 🔴 **A VACUOUS TEST WRITTEN WHILE HOLDING THE RULE AGAINST VACUOUS TESTS.** The
  dead-context teardown test asserted `__timerCount() === 0` in an environment where
  `startWatchers()` returns early (no `document`), so the count was 0 whether or not the
  teardown existed. It was caught by asking "what must the code do to satisfy this?",
  not by any run — it was GREEN. The fix is a precondition assertion: prove the timers
  are RUNNING (`running > 0`) before killing the context. **A guard must be shown
  REACHABLE, not merely breakable.**
- **A mock that omits one field can model a permanently-dead system.** `sw-mock.mjs`'s
  `chrome.runtime` has no `id`, and `id` is precisely the liveness tell that disappears
  on an extension reload — so `extAlive()` read `false` for the fixture's "live" case
  and the contrast the test existed to draw was unobservable. The red pointed at the
  code; the fault was the fixture. Same family as the shadow-DOM harness's three missing
  APIs recorded further up this file.
- **Mutation results, #1835 (`switch-watcher.test.mjs`, 13 tests):** 7 mutants, each the
  narrowest expression that can be wrong, **7/7 KILLED BY THEIR OWN NAMED TEST** —
  unchanged-short-circuit, the pre-supplied org list, unconditional baseline write, the
  rate floor, a cheap check that reports its own failure, the in-flight latch, the
  dead-context teardown. Negative control: unmutated = 13 pass / 0 fail. Restore +
  `filecmp` between every mutant. 🔴 **The red-at-base run is WEAK evidence here and is
  reported as such**: at `825b6f25` the file dies at the module-level
  `typeof PROBE.checkActiveOrg === "function"` guard (`pass 0 / fail 1`), which proves
  the function is NEW, not that the tests can detect a WRONG implementation. The sweep
  is what carries that claim.
- 🔴 **`webNavigation` was the obvious fix and was REJECTED — record the reason or it
  will be re-proposed.** `onHistoryStateUpdated` is event-driven and needs no polling,
  but it is SILENT when an account switch leaves the URL unchanged, and it costs a new
  permission. The cheap `/api/organizations` check fires on the actual switch rather
  than on a guess about claude.ai's routing. Operator chose it from four options.
- **A content script CANNOT see the page's own `history.pushState`.** The isolated world
  shares the DOM but not the page's globals, so patching `history` from a content script
  observes nothing and `popstate` covers only back/forward. That is why #1835 polls
  `location.href` (a string compare, no request) instead of intercepting.
- **The node ESM cache trap does NOT apply to this sweep** — `node --test` runs each
  test FILE in its own process, so a mutated source is genuinely re-read. Recorded
  because the `.pyc` analogue (a SURVIVED mutant that never ran) is a standing hazard in
  this repo's python sweeps and the reflex to worry about it is correct.
- 🔴 **A HANDOFF PR CAN BE OPEN OVER THE DOC YOU ARE ABOUT TO UPDATE.** #1834 was open,
  `CLEAN`, and rewrote `## State now` wholesale on the same file. Writing this update off
  `origin/main` would have conflicted and silently cost one session its findings.
  **`gh pr list --state open` filtered to `claudedocs/handoff-<topic>.md` before running
  `handoff_doc.py`** — the `/resume` sweep covers ranked work, not the doc itself.
- **`clawgate_handoff.sh resolve` exited 5** (nothing resolved), so NO `clawgate-task:`
  field is recorded for this session. An unknown session id answers 200 with an empty
  array, so this cannot distinguish "touched no task" from "wrong id" — it is not a
  clean bill of health.

## How to verify
- **#1835's tests, the subset that matters** (the sweep's own negative control):
  ```bash
  nix develop ~/workspace/devrc -c node --test ~/workspace/devrc/scripts/claude-usage/tests/switch-watcher.test.mjs
  ```
  Expect `pass 13 · fail 0`. A `fail 1` naming
  *"the switch watcher is missing — this suite is asserting nothing"* means you are on a
  tree without the fix, not that the fix is broken.
- **Node tier (dev host)** — read the runner's own `RESULT:`/`SCOPE:` lines, never a
  piped exit code:
  ```bash
  nix develop ~/workspace/devrc -c bash ~/workspace/devrc/scripts/run-node-tests.sh ~/workspace/devrc
  ```
  At `114205a1`: `RESULT: PASS (exit=0)`, `SCOPE: FULL`, `tests=1720`, claude-usage
  `files=15 tests=271 floor=246`.
- **#1835 landed (by CONTENT, never by `gh pr merge`'s rc):**
  ```bash
  gh pr view 1835 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
  git -C ~/workspace/devrc grep -q checkActiveOrg origin/main -- scripts/claude-usage/extension/content_probe.js && echo present
  ```
- **Is a Brave reload actually due?** — compare the running browser against the files on
  disk, instead of guessing:
  ```bash
  for p in $(pgrep -x brave); do stat -c %y /proc/$p; done | sort | head -1   # browser start
  find ~/workspace/devrc/scripts/claude-usage/extension -type f -printf '%TY-%Tm-%Td %TH:%TM\n' | sort -r | head -1
  ```
  A file mtime NEWER than the process start means the running code is stale.
- **Is it REGISTERED, and in which profile** (run on the host you are actually using —
  this answer differs per host, and the zero is meaningless without the control):
  ```bash
  grep -l 'claude-usage'      ~/.config/BraveSoftware/Brave-Browser/*/Preferences
  grep -l 'browser-bridge-ext' ~/.config/BraveSoftware/Brave-Browser/*/Preferences   # positive control
  ```
- 🔴 **CLOSING CONDITION — what is still unverified, stated exactly:** claude.ai open
  after a Reload to **0.3.1** → (a) **switch accounts with NO page reload; the card must
  follow within ~10s** · (b) toolbar **badge** shows session % · (c) **popup** lists
  accounts · (d) a threshold **toast** fires. (a) is new with #1835; (b)–(d) were in the
  original condition and have still never been observed. The card itself and the
  other-accounts section ARE now confirmed working.
## Open investigations — live diagnosis state

### ~~The `.local` deploy UNLOADS the extension from Brave on every home-manager switch~~ SUPERSEDED 2026-09-21 — REFUTED; see the "SUPERSEDED —" block below
🔴 **The leading hypothesis in this block is WRONG and its `Next probe` has been
DELETED, because following it would have spent a session proving something already
disproved.** Why it was wrong: it reasoned from a single `.local` extension that
survived a switch (`browser-bridge-ext`) to a mechanism (`RENAME_EXCHANGE` vs the weak
`mv -T` swap), when the two differed in more than the swap. Re-measured, the `.local`
instance survived TWO switches that rewrote it (0.1.0 → 0.2.0 → 0.3.0). **STALENESS is
the real and confirmed defect; UNLOADING is not.** The measurements below are kept
because they are still the baseline the correction compares against.
- as-of: 2026-09-20
- **Symptom + exact repro:** load unpacked from `~/.local/share/claude-usage-ext`,
  then run any `home-manager switch`. The extension disappears from
  `brave://extensions`; a reload shows the old version or nothing.
- **Observed (with values):** generation 797 landed 23:04:44 and reverted the
  deployed tree to `main`'s 0.1.0 (no `content_widget.js`, empty
  `web_accessible_resources`). Of the 12 loaded unpacked extensions, **11 point
  at a repo path**; the only `.local` one is `browser-bridge-ext`, which
  survived that same switch (mtime 23:04:44, still registered).
- **Ruled out:** "a switch always unloads an unpacked extension" — `via:
  measurement`; browser-bridge-ext was replaced by the same switch and stayed
  loaded. The difference is the swap: browser-bridge uses `RENAME_EXCHANGE`
  (`mv -T --exchange`), and `nix/home.nix:916` says so in its own words — *"so
  neither a half-written tree nor a MISSING directory is ever visible at the
  path Brave loads from."* `mkUnpackedExtensionDeploy` does `mv -T dst
  dst.old.$$` then `mv -T tmp dst`, which leaves a window where the directory
  does not exist.
- **Leading hypothesis:** Brave drops an unpacked extension whose directory
  vanishes, even briefly. The weak swap creates that window; the atomic
  exchange does not.
- ~~**Next probe:**~~ **DELETED** — it asked the reader to decide on a
  `RENAME_EXCHANGE` change justified by a hypothesis that has since been refuted.

### ~~`chrome.storage.local` is 0 bytes on the laptop — the toast dedup may never have engaged~~ RESOLVED 2026-09-21 — it was the WRONG registration; see the "RESOLVED —" block below
🔴 **Its `Next probe` is DELETED and its premise was false.** The block says the laptop
is "the only host where the extension was found registered" and reads one store. There
are **TWO** registrations on that host with **separate extension ids and therefore
separate stores**: `Default` → `onglbmc…` from the repo path, and `Profile 1` →
`doiabid…` from `.local`. The 0 bytes was `Profile 1`. Re-measured 2026-09-21: Default
`3,268,444 B` / `12,456` `resetsAt`; Profile 1 `811 B` / `0`. So no report had ever
finished **in that profile** — which is true and uninteresting — while the registration
that matters had been recording all along. The kept values below are the original
Profile-1 reading.
- as-of: 2026-09-20
- **Symptom + exact repro:** on the laptop (10.42.0.100), the only host where
  the extension was found registered, its LevelDB is empty.
- **Observed (with values):** `000003.log = 0 bytes`, mtime 21:15:55, unchanged
  at 23:55. Positive-controlled against six sibling extensions in the SAME
  profile, whose stores run 846 B – 5.3 MB — so the zero is a real reading, not
  a wiring artefact.
- **Ruled out:** nothing yet — `via: assumed` that a report simply never
  completed there.
- **Leading hypothesis:** no report has ever finished on that host, so
  `lastToast` is permanently `{}` and `withinDedup` is permanently false.
- ~~**Next probe:**~~ **DELETED** — it pointed the reader at the empty `Profile 1`
  service-worker console. The actionable consequence is ranked item 2 (remove that
  registration), not a further probe.

### SUPERSEDED — "The `.local` deploy UNLOADS the extension from Brave on every home-manager switch"
- as-of: 2026-09-21
- **This retires the block of that name above. Do not act on its "Next probe".**
- **Observed (with values):** its own next probe was run. The extension was loaded from
  the base-clone repo path and survived **two** `home-manager switch`es (#1801's ship
  and #1817's ship). MEASURED after each, on the laptop, with a validated instrument
  (`browser-bridge-ext` positive control returning 2 both times): `Default` →
  `/home/zach/workspace/devrc/scripts/claude-usage/extension` still registered, AND
  `Profile 1` → `~/.local/share/claude-usage-ext` **also still registered**.
- **Ruled out:** the leading hypothesis — *"Brave drops an unpacked extension whose
  directory vanishes, even briefly"* — `via: measurement`. The `.local` copy is the one
  `mkUnpackedExtensionDeploy` swaps with the weak non-atomic `mv -T`, and it survived
  both switches while being rewritten (0.1.0 → 0.2.0 → 0.3.0). If the weak swap dropped
  extensions, that registration would be gone. It is not.
- **What was REAL in the original block:** the `.local` copy goes **stale**, which is a
  different defect and is confirmed. Measured 2026-09-20: it read `version 0.1.0`,
  `content_scripts: ["content_probe.js"]`, `web_accessible_resources: []` while the repo
  path read 0.2.0 with both — because a `home.file` target moves on `home-manager
  switch` ALONE.
- **Next probe:** none — the arc moved to the repo path, which makes this moot. The
  remaining actionable half is ranked item 2 (delete the `.local` load-path claim from
  the docs).

### RESOLVED — "`chrome.storage.local` is 0 bytes on the laptop"
- as-of: 2026-09-21
- **This retires the block of that name above.**
- **Root cause: there are TWO extension instances in TWO Brave profiles, each with its
  own independent `chrome.storage.local`.** The 0-byte reading was the `Profile 1`
  instance loaded from `~/.local/share/claude-usage-ext` (id `doiabid…`). The `Default`
  instance loaded from the repo path (id `onglbmc…`) has been recording all along.
- **Observed (with values),** laptop, 2026-09-20:
  - `Default/Local Extension Settings/onglbmcagkpoaapfeeoepblcbeanfanl` → **644,913 B**,
    `000003.log` 644,913 B, last written 17:15:35, **266** `resetsAt` occurrences,
    **6** distinct UUID-shaped values, 9 `lastActiveOrg` writes.
  - `Profile 1/…/doiabidngiihgfkpgdcmeccjohjgfmoe` → 811 B total, `000003.log` **0 B**.
  - Positive control, same enumeration: sibling stores in `Default` run 14 MB–33 MB.
- **Ruled out:** "no report has ever finished on that host" — `via: measurement`; 266
  `resetsAt` and a 645 KB store say otherwise for the `Default` instance.
- ⚠ **6 UUID-shaped strings is an INDICATOR, not a verified account count** — some may
  be other identifiers. Do not quote it as "6 accounts"; a fix round was made to retract
  exactly that claim in five places.
- **Next probe:** none for the diagnosis. The consequence is ranked item 2 — remove the
  `Profile 1` registration, or the two stores keep splitting the data and no popup can
  ever show all accounts.

### Does claude.ai's account switcher perform a real document load? UNMEASURED, and #1835's whole diagnosis rests on the answer
- as-of: 2026-09-21
- **Symptom + exact repro:** switch accounts inside one claude.ai tab; the widget keeps
  showing the previous account's numbers until a full page reload.
- **Observed (with values):** the operator's two reports (quoted verbatim in
  `State now`). Plus the trigger inventory read out of the code: `content_probe.js`
  auto-runs only under `if (typeof document !== "undefined")` at `document_idle`;
  `service_worker.js` `shouldProbeTab` requires `tab.status === "complete"`;
  `REPROBE_PERIOD_MIN = 15`. Nothing else calls `runProbe`.
- **Ruled out:** "the widget skips a repaint while collapsed" — `via: code`, `render()`
  is driven by a 30s `setInterval` and a `storage.onChanged` listener, and `collapsed`
  only selects `paintCollapsed` vs `paintExpanded`. · "a probe overwrites the other
  accounts" — `via: code`, `handleReport` does
  `accounts[res.orgUuid] = mergeStored(prev, fresh, now)` over the map it read from
  storage. · "the cycle was rate-limited by `PROBE_MIN_INTERVAL_MS`" — `via: code`, it
  is 30s and gates only `shouldProbeTab`, and `service_worker.js`'s own comment states
  it does not gate the content script's auto-run.
- **Leading hypothesis:** the switcher is a client-side route change, so no
  `document_idle` and no `onTabUpdated(complete)`. 🔴 **This is inferred from the
  operator's report, NOT measured** — if the switcher DOES navigate, #1835 fixes a real
  cost problem but not the reported symptom, and the true cause is elsewhere.
- **Next probe:** in a claude.ai tab, before switching, run
  `performance.getEntriesByType("navigation")[0].startTime` and set
  `window.__cuMark = Date.now()`; switch accounts; check whether `window.__cuMark`
  survived (it does NOT across a real document load) and whether `location.href`
  changed. Two facts, one switch, and they settle both the mechanism and whether an
  href poll can see it.
