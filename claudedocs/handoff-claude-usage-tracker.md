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
- **#1801 MERGED** — squash `52571758`, `mergedAt: 2026-09-20T06:13:30Z`. All four
  Tekton checks were `SUCCESS` on head `37f45c9d` (`cairn-client-runs`, `gotests`,
  `nodetests`, `pytests`), `mergeStateStatus: CLEAN` — no red to adjudicate.
  🔴 Verified **by CONTENT, not by rc** (the #1803 trap): `state: MERGED`, a real
  `mergedAt`, and `git cat-file -e origin/main:scripts/claude-usage/extension/content_widget.js`
  succeeds. ⚠ The head had MOVED since the last doc — `eda25023` → `37f45c9d`, a
  merge of `origin/main` taking #1806's toast suppression alongside the widget. The
  round-3 anchor `d2c68d20..eda25023` is therefore stale; the PR is merged and the
  ladder is moot, but any retrospective read of it must re-anchor to `37f45c9d`.
- **Base clone `~/workspace/devrc` SYNCED** — `merge --ff-only` advanced
  `7739ba9e..52571758`, 18 files / +2146. Still `main`, ↑0↓0, carrying only the two
  untracked `claudedocs/scope-chief-*` files that belong to another session.
- **Earlier arc, unchanged:** #1792 MERGED (`da695960`) and shipped to both hosts;
  #1806 MERGED (`7d323b48`) — the duplicate-notification fix; #1803 CLOSED unmerged,
  superseded by #1804, which fixed the same red the other way: **it pins opencode back
  to 1.18.29 via a frozen input, because 1.18.30 cannot run a prompt.** That pin is
  durable — do not "modernise" the input without re-testing `test_opencode_engine.py`.
- **Load-path pre-flight PASSED** at
  `/home/zach/workspace/devrc/scripts/claude-usage/extension`: manifest is **v0.2.0**
  and **all 11 files it names resolve on disk** — `content_probe.js`,
  `content_widget.js`, `service_worker.js`, `popup.html`, `icons/icon-{16,48,128}.png`,
  and `lib/{widget,timefmt,format,severity}.js`. 🔴 `lib/severity.js` IS in
  `web_accessible_resources` — that omission is what made the widget dead on arrival
  in round 1, so this is the specific check worth keeping.
- 🔴 **THE EXTENSION IS STILL NOT REGISTERED IN BRAVE — re-measured AFTER the merge, and
  this remains what invalidates every "reload and check" instruction.**
  `grep -l 'claude-usage' ~/.config/BraveSoftware/Brave-Browser/*/Preferences` returns
  nothing. The earlier, wider measurement stands: absent from **all four profiles**
  (`Default`, `Profile 2/3/4`), searched **by path AND by manifest name**, while the
  same search finds the 9 other unpacked extensions in `Default`. Positive-controlled
  again here: the identical search for `browser-bridge-ext` returns
  `Default/Preferences` and `Profile 2/Preferences`. So the zero is a real reading, not
  a search wired to nothing — and step 2's "remove any existing `claude-usage-ext`
  entry" is a **no-op**, there is nothing to remove.
- **Deploy/verify status, honestly:** the code is merged, tested and sitting at a load
  path that resolves. **Nothing has still ever been observed working in a browser.**
  The closing condition is **NOT met**, and the one unmet clause is the click path.
- **Why this session could not close it:** registering an unpacked extension goes
  through a native GTK file dialog — outside anything browser-bridge can drive (its
  CDP surface is eval/screenshot/input/emulate), and driving it by hand would take the
  operator's screen. Handed over with exact steps; see `## How to verify`.

## Next steps (ranked)
1. **RUN THE REAL CLICK PATH — the arc's closing condition, never yet run.**
   `brave://extensions` → Developer mode on → **Load unpacked** → in the file chooser
   press **Ctrl+L** and paste
   `/home/zach/workspace/devrc/scripts/claude-usage/extension` → open/reload a
   `claude.ai` tab. Three things must be true: widget card bottom-right with
   Session/Weekly bars and a header tone dot · toolbar badge showing session % ·
   popup listing accounts. Capture the service-worker console from
   `brave://extensions` on any misbehaviour BEFORE diagnosing.
   `forcing: user` — no test can prove an MV3 content script mounts, and the load
   step needs a human at the file dialog.
2. Give `mkUnpackedExtensionDeploy` the `RENAME_EXCHANGE` swap that browser-bridge
   uses (`nix/home.nix`), so `.local` stops unloading extensions; **or** delete the
   `.local` claim from the manifest comment and `scripts/claude-usage/README.md` and
   say "load from the repo". Today those files document a path that unloads itself —
   and that is no longer theoretical, see the 0.1.0 measurement in Gotchas.
   `forcing: regression` — the current helper actively breaks a loaded extension.
3. Re-propose the probe-dedup cut from #1806 (one page load still costs
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

## How to verify
- The widget is at the load path Brave will read:
  ```bash
  ls /home/zach/workspace/devrc/scripts/claude-usage/extension/content_widget.js
  ```
- Every manifest-declared file resolves there (the round-1 `severity.js` class):
  ```bash
  cd /home/zach/workspace/devrc/scripts/claude-usage/extension && python3 -c "
  import json,os
  m=json.load(open('manifest.json'))
  for p in m['content_scripts'][0]['js']+[m['background']['service_worker'],m['action']['default_popup']]+m['web_accessible_resources'][0]['resources']+list(m['icons'].values()):
      print(('OK  ' if os.path.exists(p) else 'MISS'),p)"
  ```
- **Is it actually REGISTERED?** — the check this arc keeps needing:
  ```bash
  grep -l 'claude-usage' ~/.config/BraveSoftware/Brave-Browser/*/Preferences
  grep -l 'browser-bridge-ext' ~/.config/BraveSoftware/Brave-Browser/*/Preferences   # positive control
  ```
  🔴 Never quote the first zero without the second line returning hits — a bare zero
  from an unvalidated search is what let "reload it" be said three times.
- #1801 really merged (by CONTENT, never by `gh pr merge`'s rc):
  ```bash
  gh pr view 1801 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
  git -C /home/zach/workspace/devrc cat-file -e origin/main:scripts/claude-usage/extension/content_widget.js && echo present
  ```
- Node tier (dev-host): `nix develop ~/workspace/devrc -c bash scripts/run-node-tests.sh`
  → read the runner's own `RESULT:` / `SCOPE:` lines, never a piped exit code.
- Sandbox tier (the one Tekton runs):
  `nix build ~/workspace/devrc#checks.x86_64-linux.nodetests --no-link -L`.
  A build that prints NOTHING is the CACHED case, not a pass.
- **Closing condition (the only clause left):** claude.ai open → card bottom-right,
  header tone dot, Session/Weekly bars, live countdowns; badge %; popup lists accounts.
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
