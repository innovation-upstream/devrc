# Handoff: browser-bridge-flows — 2026-09-20

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. `scope-absent`/`scope-empty` means nothing is recorded yet: ordinary, not an error.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Give the browser bridge a one-vocabulary per-host FLOWS layer — rename
`site_notes`→`site_flows` + `reference/sites/`→`flows/` (SKILL.md section, server, tests,
docs) — and seed measured flows docs for `discord.com` and `app.slack.com` so a fresh
session can answer real operator queries ("recent DMs with Koen", "datapacket thread
activity in Slack") without re-deriving selectors.
- **closing-condition:** `check` — after merge + `scripts/ship.sh` (both hosts), a
  `context` on a registered-host tab carries `site_flows: flows/<host>.md` on BOTH hosts
  AND `nix develop ~/workspace/devrc -c python3 -m pytest ~/workspace/devrc/scripts/browser-bridge/tests/test_site_flows.py ~/workspace/devrc/scripts/browser-bridge/tests/test_skill_size.py -q` is green on the shipped tree.

## State now
- Branch `handoff-resume-prune-proposal` — SHARED and busy: open PR **#1815** belongs to the
  handoff-skill workstream, NOT this one; foreign commits `e1ed617d`, `fde7065c`, `ac6d794e`
  landed during this session from other sessions.
- DONE this session, **all UNCOMMITTED** in the worktree (20 modified + 3 untracked):
  - Rename: `reference/sites/`→`flows/` (content half; the pure rename itself was swallowed
    by foreign commit `e1ed617d`, see below); envelope field `site_notes`→`site_flows`;
    `_SITES_DIR`→`_FLOWS_DIR`, `_annotate_site_flows`, env `BROWSER_BRIDGE_FLOWS_DIR`;
    SKILL.md FLOWS section (12,036 B ≤ 12,038 budget); test module `test_site_notes.py`→
    `test_site_flows.py`; agent.md §, css-hit-test.md, 6 site docs, `_index.json` comment,
    tool impl/test pins, skill-audit/subsystem-audit docstring+fixture, conftest ledgers.
  - New: `flows/discord.com.md`, `flows/app.slack.com.md` (seed docs, measured facts only,
    unmeasured items tagged `⚠`), registry keys `discord.com` + `app.slack.com`,
    `claudedocs/proposal-discord-slack-flows.md`.
- Verified: full browser-bridge pytest **989 passed**; node tier **1608 passed, SCOPE: FULL**;
  outlier modules (skill-audit, subsystem-audit, doc-path-rot, skill-descriptions, cairn
  verb ledger) **280 passed**; SKILL.md byte gate green; production default-path probe
  resolves `flows/` and emits `site_flows` (civitai + discord + slack suffix keys).
- Deploy status: **NOT deployed.** Both hosts' running bridges execute the OLD deployed
  server.py (old field/dir). `ship.sh` (switch + X-Restart-Triggers restart) is the deploy
  path; annotation for the new keys fires only after it.

## Open investigations — live diagnosis state
### Slack client-v2: fresh hidden tabs NEVER hydrate (discord.com does)
- as-of: 2026-09-20
- **Symptom + exact repro:** owned tab → `nav https://app.slack.com/client/T05SM1Z372B/C095BTGHK5G`
  → `wake` → `text` stays rail-only ~300 B forever. On the laptop's `work` instance.
- **Observed (with values):** 4 retries (sleep 4) → `textlen=321` every time; inline
  `text --wake=6000` → `woke=True`, `len=223`; trusted `click a[href*="C095BTGHK5G"]`
  (a message permalink, "Open in channel") → `click ok=True`, after-click `len=273`. Same
  reads on the operator's LIVE tab hydrate fully (rail 1,767 B → full dated messages).
  Discord contrast, same session: owned hidden tab hydrates ~10 s (0 `[role=article]` at 4 s,
  11 at ~10 s, three-probe loop).
- **Ruled out:** extension not injecting on Slack — `js --wake "1+1"` → `2` (CDP path works).
  via: measurement
- **Ruled out:** settle being too short — `--wake=6000` still rail-only. via: measurement
- **Leading hypothesis:** client-v2 gates message-list render on REAL document
  visibility/focus; wake's emulation (`Page.setWebLifecycleState` +
  `Emulation.setFocusEmulationEnabled`) satisfies `visibilityState` but not the real check.
- **Next probe:** on the OWNED tab, `$BB --instance work --tab <ownedId> activate` (a REAL
  single focus steal — operator's call), wait ~3 s, `text --max-bytes 400`. Hydrated ⇒ the
  gate is real visibility, and drawer/Threads flows become measurable in owned tabs.
### Slack thread-drawer + Threads rail + workspace switcher — unmeasured
- as-of: 2026-09-20
- **Symptom + exact repro:** channel-level thread summary works (wake'd `text` blocks:
  body → `N replies` → `Last reply <age> ago` → `View thread`); thread CONTENT needs a
  trusted `click` on `View thread` — impossible on the operator's live tab (forbidden
  without approval) and impossible in a fresh owned tab (above).
- **Observed (with values):** selector hunt on fresh owned tab: 1 button ("Summarize
  thread"), 0 View-thread buttons, `[role=listitem]`=6, `[role=article]`=0 — the list never
  hydrated. Live-tab wake'd text carried the markers: `7 replies | Last reply 24 days` and
  `3 replies` blocks (2026-09-20).
- **Ruled out:** old `data-qa=virt-list-item`/`message-input` selectors — counted 0;
  html root carries `data-app="client-v2"`. via: measurement
- **Leading hypothesis:** all three flows are blocked by the hydration gate above, not by
  selectors (composer IS pinned: `[role=textbox]`, `ql-editor`, `data-qa=texty_input`).
- **Next probe:** after the activate escape above resolves hydration, in the OWNED tab:
  `click` a `View thread` button, read the drawer pane `text`, then enumerate the `Threads`
  rail pane the same way.
### Workbench extension lost host access (Site access) — operator fix pending
- as-of: 2026-09-20
- **Symptom + exact repro:** any injection on the WORKBENCH bridge — `text`/`js` on ANY
  host — answers `Cannot access contents of the page. Extension manifest must request
  permission to access the respective host.` (measured on discord.com AND civitai.com tabs,
  2026-09-20). Messaging ops (`health`/`tabs`/`context`) answer fine.
- **Observed (with values):** manifest carries `<all_urls>`; `health` shows
  `extension_stale:false`, build current `66b98084daecd880`. Operator ran the fix on the
  LAPTOP (brave://extensions → Browser Bridge → Details → Site access → On all sites) and
  laptop injections then worked — the same fix on the workbench is unapplied.
- **Ruled out:** stale build / dropped extension — via: measurement (health verdict above).
- **Leading hypothesis:** Brave per-extension Site access is restricted on the workbench.
- **Next probe:** operator repeats the Site access fix on the workbench, then
  `$BB --tab <any civitai tab> text --max-bytes 100` must answer.

## Next steps (ranked)
1. Commit the rename's CONTENT half (the 20 modified + 3 untracked bridge files; foreign
   commit `e1ed617d` already carries the pure renames with none of the code that makes them
   true — that sha is red in isolation, its server.py reads a directory it deleted).
   forcing: regression
2. Open the PR for this work, merge, run `scripts/ship.sh`, then live-verify per
   `reference/security-ops.md`: `context` on the laptop's discord + slack tabs must carry
   `site_flows: flows/<host>.md`. forcing: gate
3. Operator: repeat the Site access fix on the WORKBENCH extension (blocker measured above;
   the discord/slack tabs live on the laptop, but the workbench bridge is blind until then).
   forcing: user
4. Measure the Discord composer/menus flows in an OWNED tab (trusted click → type → clear,
   `key Enter` never without approval; menu toggles assert `aria-expanded`), then retire the
   `⚠ UNMEASURED` lines in `flows/discord.com.md`. forcing: user
5. Measure Slack drawer/Threads/workspace-switcher once hydration is solved (depends on
   item in the investigations above). forcing: none

## Defects (batched)
- None this session (no audit round ran on this work yet).

## Gotchas / decisions / dead-ends
- `e1ed617d` ("stt: CLI…") is a FOREIGN session's commit that swallowed this session's
  STAGED pure renames — the sha is broken in isolation; the content commit (next-steps 1)
  is what makes the tree true again. Do not rebase/amend it (another session owns it).
- TWO bridges, one name: both hosts are `nixos`; `whoami`'s `host.label` disambiguates
  (laptop bridge = 2 instances `personal`/`work`; workbench = 1 instance `work`). The
  Discord/Slack tabs live on the LAPTOP (`work` instance): slack tab 484081034, discord tab
  484081047. Drive the laptop bridge via `ssh -o BatchMode=yes 10.42.0.100 'bash -s' < script`
  (quoting-safe pattern for `$BB` js probes).
- Slack: plain `js`/`eval` is CSP-null (`value: null`, no error) — escape hatch `js --wake`
  (CDP `Runtime.evaluate` bypasses page CSP; measured `1+1` → `2`). Discord has no CSP eval
  block; its MAIN-world `js` works even hidden.
- Slack client-v2: composer `[role=textbox]` (`ql-editor`, `aria-label="Message to
  <channel>"`, `data-qa=texty_input`); old `data-qa` message selectors are GONE.
- Discord: messages are `[role=article]`; DMs are `/channels/@me/<dmId>`; find a DM by
  reading `a[href*="/channels/@me/"]` anchors' `aria-label`s (list order = recency;
  `(direct message)` vs `(group message)`); fresh nav hydrates ~10 s — retry loop, never
  conclude empty early.
- `activate` STEALS the operator's screen — never routine; the only remaining unmeasured
  escape for the Slack hydration gate.
- The proposal doc `claudedocs/proposal-discord-slack-flows.md` records the plan + open
  questions (which guilds/workspaces matter; agent-mode stance on chat content).

## How to verify
```bash
nix develop ~/workspace/devrc -c python3 -m pytest ~/workspace/devrc/scripts/browser-bridge/tests/test_site_flows.py ~/workspace/devrc/scripts/browser-bridge/tests/test_skill_size.py -q
nix develop ~/workspace/devrc -c bash ~/workspace/devrc/scripts/run-node-tests.sh   # SCOPE: FULL
wc -c ~/workspace/devrc/scripts/browser-bridge/SKILL.md   # must be ≤ 12,038
# after ship.sh, live-verify (laptop):
ssh -o BatchMode=yes 10.42.0.100 'BB=~/workspace/devrc/scripts/browser-bridge/browser; $BB --instance work --tab 484081047 context'
#   → envelope must carry "site_flows": "flows/discord.com.md"
```
