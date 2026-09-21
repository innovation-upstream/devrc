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
- **Earlier arc, carried forward:** #1792 MERGED (`da695960`); #1806 MERGED
  (`7d323b48`) — the duplicate-notification fix; **#1803 CLOSED unmerged**, superseded
  by #1804, which fixed the same red the other way: **it pins opencode back to 1.18.29
  via a frozen input, because 1.18.30 cannot run a prompt.** That pin is durable — do
  not "modernise" the input without re-running `test_opencode_engine.py`.
- **#1801 MERGED** (squash `52571758`) — the in-page widget + real icon.
- **#1808 MERGED** (squash `62bd4d9a`) — the prior handoff update.
- **#1817 MERGED** (squash `4487d2dd`, `2026-09-21T05:05:45Z`) — the all-accounts
  availability section. Verified by CONTENT, never by `gh pr merge`'s rc:
  `lib/availability.js` present on `origin/main`, manifest reads **0.3.0**.
- **SHIPPED to both hosts.** `scripts/ship.sh` → both at `4487d2dd`, each per-host
  line read individually (not just the final verdict): `✅ VERIFIED — on branch main
  at origin/main + switched`, 0 dangling, 0 stale, no skips, no dirty path read by
  nix. The laptop's earlier `scripts/opencode/opencode.jsonc`-in-the-artifact warning
  is gone.
- **Laptop load path is live at v0.3.0**: `content_scripts: [content_probe.js,
  content_widget.js]`, WAR carries all five `lib/*.js` including `availability.js`
  (31,375 B). Registration survived both `home-manager switch`es.
- 🔴 **THE CLOSING CONDITION IS STILL NOT MET, AND ONE ITEM IS LEFT: the real click
  path.** Brave is still running the **0.2.0** code it read at load time — unpacked
  extensions do not hot-reload. Nothing in this feature has EVER been observed working
  in a browser.
- 🔴 **Scope of every verification claim on this arc: MODEL-LEVEL ONLY.** ~4,200 lines
  and 258 tests, five audit rounds, a dozen mutation batteries — all `node --test`
  against the pure model, plus CSS guards that read `content_widget.js` as TEXT. The
  shadow-DOM harness has **no CSS cascade**, and audit rounds 3 and 4 were *entirely
  about colour*. Nothing here is evidence about pixels.
- **Audit ladder on #1817 is CLOSED at round 4** — rounds 0/1/2/3/4, each producing
  real findings, stopped on the attribution gate's own logic: the final fix changed
  **zero payload lines** (one test assertion + comments), so a round 5 would audit
  scaffolding the ladder itself wrote. Three `audit-claims` blocks are posted on the PR
  (rounds 1, 2, 3) as ISSUE comments — the only surface `audit-dispatch.py` reads.
- **Tier verdicts at the final head `74a9f1b5`:** all four Tekton checks `success`
  (`pytests`, `nodetests`, `gotests`, `cairn-client-runs`), read off
  `/commits/<sha>/status`; `check-runs` is 0, which is normal for this repo.
  Independently re-measured by the dispatching session: 14 files / **258 tests, 258
  pass, 0 fail**; floor `13|246` reproduces the runner's formula.
- **Base clone has moved well past the merge** (`b84745f2` at time of writing) — other
  sessions are active in `~/workspace/devrc`. It sits on `main`, so this doc was landed
  from a worktree.

## Next steps (ranked)
1. **RELOAD THE EXTENSION AND RUN THE REAL CLICK PATH — the arc's closing condition,
   still never run.** On the laptop: `brave://extensions` → **Reload** on *Claude Usage
   Tracker* in the **Default** profile → confirm the card reads **0.3.0** (the only
   signal Brave took the new code; `getManifest()` describes the DIRECTORY, so a version
   string alone proves the directory is right, not that the code was re-evaluated) →
   open/reload `claude.ai`. Check: widget card bottom-right, other-accounts section
   ranked most-available-first, a freed-up account reading `AVAILABLE` not its stale
   percentage, badge %, popup lists accounts. Capture the service-worker console on any
   misbehaviour BEFORE diagnosing.
   ⚠ Expect a **dimmed card with a saturated green or red dot** for a stale record. That
   is correct — it is what makes the card agree with the list — and "fixing" it by
   restoring the grey re-opens the defect four audit rounds closed.
   `forcing: user` — no test can prove an MV3 content script mounts, and only a human
   can click Reload.
2. **Remove the `Profile 1` registration** pointing at `~/.local/share/claude-usage-ext`
   (`brave://extensions` in that profile), **and delete the `.local` load-path claim**
   from `scripts/claude-usage/extension/manifest.json`'s comment and
   `scripts/claude-usage/README.md`. Two instances = two independent stores, so whichever
   popup is open can only ever show accounts visited in that profile.
   `forcing: user` — this is a live cause of the symptom the operator reported
   ("only showing the currently logged in one").
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
   `2x(/api/organizations + one /usage per org)`), on its own PR with its own
   justification.
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

## How to verify
- **#1817 landed (by CONTENT, never by `gh pr merge`'s rc):**
  ```bash
  gh pr view 1817 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
  git -C ~/workspace/devrc cat-file -e origin/main:scripts/claude-usage/extension/lib/availability.js && echo present
  ```
- **Both hosts carry it** — read every per-host line, not the final verdict:
  ```bash
  bash ~/workspace/devrc/scripts/drift-check.sh     # READ-ONLY; ship.sh is the fixer
  ```
- **The load path is v0.3.0 with the new module:**
  ```bash
  python3 -c "import json;m=json.load(open('/home/zach/workspace/devrc/scripts/claude-usage/extension/manifest.json'));print(m['version'], m['content_scripts'][0]['js'], [r for w in m['web_accessible_resources'] for r in w['resources']])"
  ```
- **Is it REGISTERED, and in which profile** (run on the host you are actually using —
  this answer differs per host, and the zero is meaningless without the control):
  ```bash
  grep -l 'claude-usage'      ~/.config/BraveSoftware/Brave-Browser/*/Preferences
  grep -l 'browser-bridge-ext' ~/.config/BraveSoftware/Brave-Browser/*/Preferences   # positive control
  ```
- **Node subset** (prefer this over the full tiers — see the contention gotcha).
  🔴 Use an EXPLICIT file list and assert the count is 14 first:
  ```bash
  files=("${(@f)$(find ~/workspace/devrc/scripts/claude-usage/tests -name '*.test.mjs' | sort)}")
  [ "${#files[@]}" -eq 14 ] && nix develop ~/workspace/devrc -c node --test "${files[@]}"
  ```
  Expect `tests 258 · pass 258 · fail 0`; floor in `scripts/run-node-tests.sh` is `13|246`.
- **CI, on BOTH surfaces** — neither is a superset of the other, and read `state` not colour:
  ```bash
  SHA=$(gh pr view <n> --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)
  gh api "repos/innovation-upstream/devrc/commits/$SHA/status"     --jq '[.statuses[]|"\(.context)=\(.state)"]'
  gh api "repos/innovation-upstream/devrc/commits/$SHA/check-runs" --jq .total_count
  ```
- 🔴 **CLOSING CONDITION (the only clause left):** claude.ai open → widget card
  bottom-right with the other-accounts section, header tone dot, Session/Weekly bars,
  live countdowns; badge %; popup lists accounts. **Requires a Brave Reload first** —
  unpacked extensions do not hot-reload.
## Open investigations — live diagnosis state

### The `.local` deploy UNLOADS the extension from Brave on every home-manager switch
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
- **Next probe:** load from the base-clone repo path, confirm it survives a
  `home-manager switch`, then decide whether to give
  `mkUnpackedExtensionDeploy` the same `RENAME_EXCHANGE` (it would fix
  `discord-embed-ext` too).

### `chrome.storage.local` is 0 bytes on the laptop — the toast dedup may never have engaged
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
- **Next probe:** open that profile's service-worker console and run
  `chrome.storage.local.get(null)`.

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
