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
- **#1792 MERGED** (`da695960`) and shipped to both hosts; **#1806 MERGED**
  (`7d323b48`) — the duplicate-notification fix; **#1803 CLOSED unmerged**,
  superseded by #1804 which fixed the same red the other way (pins opencode
  back to 1.18.29 via a frozen input, because 1.18.30 cannot run a prompt).
  `main` is GREEN — measured at `7ef01c05`: `opencode --version` 1.18.29,
  `test_opencode_engine.py` 25 passed.
- **#1801 OPEN** — in-page widget + real icon, head `eda25023`, worktree
  `~/workspace/devrc-cu-widget` (branch `feat/claude-usage-widget`). Audit
  ladder at **round 2 complete, round 3 NOT run**. Both of its CI reds are
  fixed (stale base predating #1804; my own sandbox-blind `git ls-files`
  test). Node tier on the merged tree: 12 files / 149 tests, floor `11|142`,
  TOTAL 1598/1598, `RESULT: PASS`, `SCOPE: FULL`.
- 🔴 **THE EXTENSION HAS NEVER BEEN REGISTERED IN BRAVE — and this invalidates
  every "reload and check" instruction given this session.** MEASURED: absent
  from all four profiles (`Default`, `Profile 2/3/4`), searched by path AND by
  manifest name. Instrument validated — the same search finds the 9 other
  unpacked extensions in `Default`, whose `Preferences` was written minutes
  before the read. So the click path has never run, and the arc's closing
  condition is **NOT met**.
- **Deploy/verify status, honestly:** the code is merged-or-ready and tested;
  **nothing has ever been observed working in a browser.**
- Base clone `~/workspace/devrc` was behind 2 with two untracked
  `claudedocs/scope-chief-*` files (another session's — leave them).

## Next steps (ranked)
1. Merge **#1801** once its four checks are green. Read the failing test's name
   before believing any red — measured ~42% noise on this repo.
   `forcing: user` — the operator asked for it merged.
2. Sync the base clone and LOAD FROM IT:
   `git -C ~/workspace/devrc fetch origin && git -C ~/workspace/devrc merge --ff-only origin/main`,
   then `brave://extensions` → remove any `claude-usage-ext` entry → Load
   unpacked → **`/home/zach/workspace/devrc/scripts/claude-usage/extension`**.
   Verify the manifest resolves there BEFORE telling the operator it is ready.
   `forcing: user` — he chose the base clone as the load path.
3. VERIFY THE REAL CLICK PATH — the arc's closing condition, never yet run:
   claude.ai open → widget card bottom-right with Session/Weekly bars + header
   tone dot → badge % → popup lists accounts. Capture the SW console on any
   misbehaviour before diagnosing.
   `forcing: user` — no test can prove an MV3 content script mounts.
4. Give `mkUnpackedExtensionDeploy` the `RENAME_EXCHANGE` swap browser-bridge
   uses (`nix/home.nix`), so `.local` stops unloading extensions; or delete the
   `.local` claim from the manifest comment and README and say "load from the
   repo". Today those files document a path that unloads itself.
   `forcing: regression` — the current helper actively breaks a loaded extension.
5. Round 3 delta audit on #1801 if it has not merged: round 2 returned findings
   that needed fixing, so the ladder is not finished. Anchor
   `d2c68d20..eda25023`; claims blocks for rounds 1 and 2 are posted on the PR.
   `forcing: gate` — the ladder's own stop rule.
6. Re-propose the probe-dedup cut from #1806 (one page load still costs
   `2x(/api/organizations + one /usage per org)`), on its own PR with its own
   justification.
   `forcing: none`

## Defects (batched)
<!-- This heading REPLACES, it does not append — carry prior rounds forward. -->
- #1792 review round 1 (all fixed in `7e1bc585`, suites re-run green): stale
  proposal status line → IMPLEMENTED; `weekly.lockedReason` unconsumed →
  weekly-lock alert added + pinned (the reviewer's premise that the API lacks
  the field was WRONG — recon shows `locked_reason` on every window);
  `badgeFor(null, truthy-org)` TypeError → guarded + pinned; `pickActiveOrg`
  ran on the RAW org list (could pick an org validation then drops) → now picks
  from the validated list, `isActive` carried through `validateOrgs`, F4 pin
  added; `normalizeUsage` docstring narrowed to write+read paths. One follow-on
  test bug (missing `ORG_C`) fixed same session — 87/87.
- Round 0 on #1801: `make-icons.sh` cut (unrun, and the silent failure its
  verify step defended against was created by its own first draft); the
  badge/widget severity predicates consolidated into `lib/severity.js`.
- Round 1 on #1801: 🔴 the widget was **dead on arrival** — `lib/severity.js`
  missing from `web_accessible_resources`, the rejection swallowed by a catch.
  Plus: silent `import()` failure, frozen-forever card on a dead context,
  `pointer-events` over claude.ai's composer, `severityTone` returning
  `Object`'s constructor, `severityColor` deleted, `action.default_icon` added.
- Round 2 on #1801: 🔴 the record tone vanished from the card (red badge above
  three green bars); `retire()` force-expanded a collapsed widget; the manifest
  guard was blind to dynamic imports; its `existsSync` claimed trackedness it
  could not see; a vacuous positive-control assertion removed.
- Round 0 on #1806: the whole diagnosis was fitted to a COUNT; `reportsSettled()`
  deleted as measured-redundant; the probe-dedup half reverted as unasked-for.

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

## How to verify
- Node tier (dev-host): `nix develop ~/workspace/devrc -c bash scripts/run-node-tests.sh`
  → `RESULT: PASS`, `SCOPE: FULL`, claude-usage row `tests=149 floor=142`.
  Read the runner's own `RESULT:` line, never a piped exit code.
- Sandbox tier (the one Tekton gates on):
  `nix build ~/workspace/devrc#checks.x86_64-linux.nodetests --no-link -L`.
  A build that prints NOTHING is the CACHED case, not a pass.
- The widget is deployed where Brave can load it:
  `ls /home/zach/workspace/devrc/scripts/claude-usage/extension/content_widget.js`
- Is it actually REGISTERED? (the check this session lacked):
  ```bash
  grep -l 'claude-usage' ~/.config/BraveSoftware/Brave-Browser/*/Preferences
  ```
  Positive-control it with `browser-bridge-ext`, which IS loaded — a bare zero
  from an unvalidated search is what let "reload it" be said three times.
- Real click path (closing condition): claude.ai open → card bottom-right,
  header tone dot, Session/Weekly bars, live countdowns; badge %; popup lists
  accounts.
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
