# Handoff: browser-bridge emulate + extension staleness — 2026-08-04

## Goal
Give the autonomous `browser agent` the `emulate` op so it can run width-dependent
(responsive) verification passes (#316) — and fix the two defects that hunt surfaced:
the emulated viewport was permanently sticky per tab (#319), and `extension_stale`
could not tell a stale profile from a current one (#324).

**All three are shipped, closed, and live-verified. There is no queued work.**

## State now
- Branch: `main` @ **`c0dc40a`**. Both hosts converged + switched (`scripts/ship.sh`).
- **Zero open issues.** Three open PRs, all from OTHER sessions — do not assume they are
  yours: #326 (`fix/resume-state-session-handoff`), #313 (`zach/rules-multiagent-lessons`),
  #294 (`zach/browser-skill-js-wake-and-blind-agent`).
- Working tree is DIRTY with another session's WIP — `.npmrc` (deleted), `.serena/project.yml`,
  `nix/pkgs/tools/default.nix` (+1 commented line), untracked `nix/pkgs/tools/screenarc.nix`.
  **Not mine, left untouched.** The screenarc package is inert twice over: the import line is
  commented AND the file is untracked, so the flake cannot see it.

### DONE this session
| # | what | PR |
|---|---|---|
| #316 | `emulate` reachable + **default-on** for the agent (13 ops); emulation arg fields in the typed schema | #321 |
| — | `emulate --reset --recreate` — CLI-side tab-replacement workaround | #321 |
| — | raw `userAgent`/`timezone` withdrawn from the AGENT surface (`emulation_field_operator_only:<field>`); presets + operator CLI unaffected | #321 |
| — | unbroke the pytest gate; made every `--reset` claim true of the code; note text pinned by a test | #323 |
| #324 | **BUILD MARKER** that travels with the code; `extension_stale` fails closed | #327 |
| #319 | `--reset` really undoes the viewport — **arm, then clear** | #328 |

Closed unmerged: **#318** (built on a falsified premise) and **#320** (its fix was
live-measured and did not work). Both carry comments explaining why — read those before
resurrecting either branch.

### Deploy/verify status — verified, not merely deployed
- Extension **0.8.1**, marker **`73f5438f18f395d2`**, `extension_stale: false` (marker-backed)
  on BOTH profiles (`personal`, `work`) on the **laptop**.
- `browser-bridge` unit active, listener PID owned by the unit's cgroup.
- Live acceptance, both profiles, control every round:
  `control 2256 → emulate iphone-15 393 → --reset 2256 → re-nav 2256`.
- ⚠ **Workbench Brave was never reloaded.** `ship.sh` deployed the code there, but the
  extension needs a per-profile Remove + Load unpacked. Expect `extension_stale: true`
  there — which is the marker doing its job, not a defect.

## Open investigations — live diagnosis state

### Unexplained: a freshly-started Brave executed extension code that existed on no disk
Not blocking anything; the *operational* problem is solved by the build marker (#327).
Recorded so nobody re-derives it.

- **Symptom + exact repro:** after `ship.sh` + a full Brave restart, the `personal` profile
  served 0.7.2 behaviour while the `work` profile served `main`. Discriminator:
  `browser --instance <p> --tab <id> emulate --reset` — the 0.7.2 build emits a
  `cleared: [...]` field that `main`'s code structurally cannot produce.
- **Observed (values):**
  - `personal`: `cleared: ["Emulation.clearDeviceMetricsOverride", "Emulation.setTouchEmulationEnabled", "Emulation.setUserAgentOverride"]` + note `"…overrides CLEARED on the tab…"`
  - `work`: `main`'s note, no `cleared` field
  - BOTH reported `extension_id bgbkamdlkdleahpgdgmjipjbgmepgenk`, `extension_version 0.7.3`, `extension_stale false`
  - `grep -ra "CLEARED on the tab" ~/.local/share/browser-bridge-ext/ $DEVRC/scripts/browser-bridge/` → **no matches anywhere**
  - `ps -eo lstart,comm | grep brave` → oldest Brave process **392 s** old; deploy was **9 h** earlier
  - `personal` `instanceId` `c0e5f081` was **constant across every restart of the session**, changing only on Remove + Load unpacked
- **Ruled out:** stale service worker surviving a restart (browser processes were minutes old);
  stale `browser-bridge` service (restarted, new PID, behaviour unchanged); wrong directory
  (`extension_id` is `sha256(abs path)[:32]` mapped `0-f`→`a-p`; computed id matched
  `/home/zach/.local/share/browser-bridge-ext` exactly); stale server-side note (the string is
  in no `server.py`); a grep false-negative (re-checked with `grep -a` and short fragments —
  long strings are `+`-concatenated across lines, whole-sentence greps silently miss).
- **Leading hypothesis:** per-profile Chromium caching of unpacked-extension resources that a
  browser restart does not invalidate. **Not established.**
- **Next probe:** reproduce deliberately — deploy a marker-only change, restart Brave WITHOUT
  Remove/Load-unpacked, and read `browser --instance <p> ping`'s `buildMarker`. A stale marker
  after a clean restart confirms it and gives a repro Chromium upstream would accept.

## Next steps (ranked)
1. **Reload the workbench's Brave extension** (per profile: Remove → Load unpacked
   `/home/zach/.local/share/browser-bridge-ext`) and confirm `extension_stale: false`.
   Until then the workbench runs old code and the marker correctly says so.
2. **Nothing nags about a `null` staleness verdict.** A profile never re-added sits at
   "undecidable" forever with no prompt — more honest than the old `false`, less actionable.
   Candidate home: the bar's `tlm` pill or a `whoami` hint.
3. `docs/LAYOUT.md` still describes the old version-based staleness signal. Marked
   stale-by-design and not auto-loaded, so low harm — but it is now wrong.
4. Optional: `/audit-pr` was NOT run on #327 or #328 (it was run on the earlier delta and
   found two 🔴). Those two are the ones the whole trust chain now rests on.

## Gotchas / decisions / dead-ends
- 🔴 **`clearDeviceMetricsOverride` is a NO-OP unless the same session armed an override first**,
  and it returns success either way. This is why #320 fired its clears, had them acknowledged,
  and changed nothing. Fix is `setDeviceMetricsOverride{0,0,0,false}` → `clearDeviceMetricsOverride`.
  dpr/touch/UA/media/tz are renderer-side session state that dies at detach; the viewport
  ALSO resizes the browser-side render widget, which only an armed clear undoes.
- 🔴 **`extension_version` and `extension_id` describe the DIRECTORY, not the running code.**
  `getManifest()` reads the on-disk manifest; the id is path-derived. Two profiles loading one
  directory necessarily agree on both. Use `buildMarker` / `extension_stale` — and `false` now
  means verified-current, `null` means undecidable.
- **Option 2 in #316 was structurally impossible**: the model is bound by `browser.js`'s typed
  `op` enum, and `OP-SET PARITY` asserts `deepEqual` both ways between that enum and
  `ALLOWED_OPS_DEFAULT`. "In the enum" ⇒ default-on. The `upload` opt-in precedent is a stub —
  `upload` is absent from the enum AND `path` is absent from the args schema, so no model can
  invoke it. Untouched, deliberately.
- **A full Brave restart is NOT a reliable extension reload.** Per-profile Remove + Load
  unpacked is. Never kill Brave from an agent — `restore_on_startup` is unset.
- **Investigate CDP behaviour in a throwaway Brave under Xvfb**, not the live one:
  `xvfb-run -s "-screen 0 1280x1024x24" brave --user-data-dir=<scratch> --remote-debugging-port=<free>`.
  Reproducing #319 there with **no extension in the loop** is what killed the "extension bug",
  "`chrome.debugger` bug" and "compositor bug" theories in one move. Avoid `--headless` for
  render-widget bugs — it may not reproduce them.
- **Three of my diagnoses were wrong** and each was corrected only by measurement: the
  exclusion rationale (#318), the missing-CDP-call root cause (#319/#320), and the
  stale-SW mechanism (#324). Public corrections are on #316, #319, #324.
- **Green suites proved nothing here.** They were green at every point something was broken,
  including twice on work I had personally verified and once through a clean adversarial audit.
  #322 shipped a RED pytest gate to `main` because only the node suite was counted.

## How to verify
```bash
# both tiers — COUNT, never read an exit code
node --test --test-reporter=tap $DEVRC/scripts/browser-bridge/tests/*.test.mjs   # 495/495 (glob form ONLY)
nix-shell -p 'python312.withPackages(p:[p.pytest p.psycopg2 p.requests p.minio])' \
  --run "python -m pytest $DEVRC/scripts/browser-bridge/tests -q"                # 467 passed, 0 failed

# is the running extension the deployed one? (marker, NOT version)
browser health        # expect marker 73f5438f18f395d2, extension_stale false

# #319 live acceptance — assert the VIEWPORT, never ok:true
browser --instance personal open https://example.com          # -> tabId
browser --instance personal --tab <id> js 'innerWidth'        # control (e.g. 2256)
browser --instance personal --tab <id> emulate iphone-15
browser --instance personal --tab <id> nav https://example.com
browser --instance personal --tab <id> js 'innerWidth'        # 393
browser --instance personal --tab <id> emulate --reset
browser --instance personal --tab <id> js 'innerWidth'        # MUST equal the control
browser --instance personal --tab <id> close                  # always clean up

# #316 agent surface
node -e "import('$DEVRC/scripts/browser-bridge/opencode/tools/browser_tool_impl.mjs')
  .then(m=>console.log(m.OP_TO_SERVER.emulate, m.ALLOWED_OPS_DEFAULT.includes('emulate'),
                       m.ALLOWED_OPS_DEFAULT.length))"       # emulate true 13
```
