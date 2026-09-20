# Handoff: tmux-reply-menu-delivery — 2026-09-20

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exits **0**
naming **#603**, but 603 is the transcript-archive card of the *tmux-webapp* arc
(`claudedocs/handoff-tmux-webapp.md`). This session worked both arcs, so the
session-scoped resolve is right that 603 was worked and wrong that it is *this* doc's
task. Do not add it; there is no clawgate task for the reply-agent work.

## Goal
Make a reply tapped on a clawgate attention card deliver **the option the operator
chose**, into the pane that is actually showing that ask — and never press a key in a
dialog that is not an `AskUserQuestion`.

- **closing-condition:** `check` — tap a non-first option on a clawgate card against a
  **real blocking `AskUserQuestion`** and observe Claude Code record *that* option
  (`● User answered Claude's questions:` / `⎿ · <q> → <chosen label>`), with the card
  reporting `delivered`. 🔴 **MET 2026-09-20 12:05 CDT** — a real trusted click on the
  **second** option of a live card delivered that option; evidence in State now.

## State now
- Branch: devrc `main` at `62d83a08`, clean but for two untracked `claudedocs/scope-chief-*.md` (they are in PR **#1783**, open).
- **MERGED + DEPLOYED this session:**
  - `c8cf7b15e` (**#1784**) — the option-1 fix: read the pane, press the digit of the row whose label the reply names.
  - `62d83a08` (**#1795**) — the invariant: a short pane clipped every top-of-modal signal, so a live ask read `text` (typed into) and a live chain read `menu` (half-answered). Now keyed on the **footer**, which does not clip.
- **Deploy verified on BOTH hosts, chain not assumed:** `ship.sh` converged both to `62d83a08` (workbench already there; laptop fast-forwarded `52571758 → 62d83a08`; 619/578 managed artifacts resolve, 0 dangling, 0 stale). The unit runs the script **directly from the working tree** (`ExecStart=… python3 %h/workspace/devrc/scripts/tmux-reply-agent`), so a `home-manager switch` does not reload it — **the restart is the deploy**. Per host: leaf cgroup = `tmux-reply-agent.service`, script sha == `origin/main`'s (`ac8e88ae960b18c5`), process start postdates the file write, old PID gone.
  - workbench `2388761 → 1420991`, laptop `2222420 → 2227340`, both `active`/`running`, `NRestarts=0`.
- **Audit ladder on #1795: 5 rounds**, ending `no high-severity findings`. Payload trajectory **171 → 218 → 107 → 22 → 0** (the last verified by `ast`: 753 statements / 45 docstrings / **708 executable, identical both sides**).
- CI at the merged head: `nodetests` 1536/1536, `gotests` 386/386, `cairn-client-runs` pass; **`pytests` red on one pre-existing test** (below).
- 🔴 **THE CLOSING CONDITION, MEASURED — 2026-09-20 12:05 CDT.** Six links, each observed, none inferred:
  1. **A real blocking `AskUserQuestion`** in scratch pane `%205` (120×40, inside the measured-correct 79+ cols / 16–40 rows region): footer `Enter to select · ↑/↓ to navigate · Esc to cancel`, cursor on `❯ 1. Apricot`, `2. Blackcurrant` unselected.
  2. **A real card**: `/api/attention` entry **17800**, `kind=question`, `tmuxPane=%205`, `host=workbench`, `options=[Apricot, Blackcurrant]`.
  3. **A real tap on the NON-FIRST option** — trusted CDP mouse click (`trusted:true`, `via:mouse`) on `#reply-attention-17800 [data-reply-option-label="Blackcurrant"]`, `data-reply-option=1`. The card's own `hx-confirm` fired and read **`Send “Blackcurrant” to workbench %205 and press Enter?`**, and the button's `hx-vals` carried `{"text":"Blackcurrant","idempotencyKey":"ui-reply:17800:workbench:%205:2595882f54e531dd"}` — the LABEL, verbatim, never an index.
  4. **The browser tier, not a machine shortcut**: `POST /ui/term/send-keys` → **200**; queue row `tier=browser actor=browser-session entry=17800 text="Blackcurrant"`, and the card reported **`state=delivered`**.
  5. **The deployed agent did it**: `claimedBy=workbench:1420991` — the PID verified at session start as the unit's `MainPID`, cgroup leaf `tmux-reply-agent.service`, script sha `ac8e88ae960b18c5` == `origin/main`'s.
  6. **The right answer landed, by the right mechanism**: the agent's own journal reads `delivered … (pane %205): menu row 2 was sent and the pane records this option as the answer`, and the pane reads
     `● User answered Claude's questions:` / `⎿ · Which fruit should the probe record? → Blackcurrant`.
  ⚠ **Scope of this claim, stated rather than implied:** ONE ask, ONE host (workbench), a single-question non-multiSelect modal at one size, cursor on row 1 and the tap on row 2. It is the discriminating case — the defect delivered row 1 for every tap — but it is one point, not a range. The laptop agent was never exercised.

## Open investigations — live diagnosis state

### CLOSED — the symptom was reproduced-then-confirmed-gone against a live ask
as-of: 2026-09-20 (closed; kept for the evidence, not as open work)
- **Symptom + exact repro:** before the fix, tapping *any* option on a clawgate attention card delivered **option 1** while the card reported `Delivered`. Repro then: raise a real `AskUserQuestion`, tap a non-first option on the card, read what Claude Code records.
- **Observed (with values):** the defect was reproduced *in process* — `tmux send-keys -t %176 -l -- 'SQLite'` then `send-keys Enter` → `⎿ · Which database should the queue use? → Postgres`. The fix was then verified at classification level — a paired 57-pane sweep gave `text 53 → 53`, **drivable menu 0 → 1**, refused `4 → 3`; 19 of 22 new test ids red at base and green at HEAD; 27- and 19-mutant sweeps with sentinels and controls.
- 🔴 **AND NOW END-TO-END, WHICH IS WHAT WAS MISSING.** See "the closing condition, measured" in State now for the six-link chain. The recorded answer was **Blackcurrant** — row **2**, the option tapped — not `Apricot`, the row the cursor sat on and the row the defect delivered every time.
- **Ruled out:** that the classifier's `0` drivable menus meant no menus were up — it meant `MENU_ROW_GAP = 4` could not span a real render's described options (measured inter-row spans 7/4/5/2, 5/3/3/4/2, 6/5/3/2 on three live modals). `via: measurement`
- **Ruled out:** that the ordinary text-prompt path was also broken — pinned across four pane states (shell prompt, Claude text prompt, transcript, blank), green at base and HEAD. `via: command`

### A browser-bridge tab's CREDENTIALED requests stall a few seconds after load
as-of: 2026-09-20
- **Symptom + exact repro:** open a bridge-owned tab on `http://192.168.50.250:30302/attention`, let it settle ~4s, then issue any same-origin request carrying the session cookie. It never completes — and the server never logs it, so it is not reaching clawgate.
- **Observed (with values):** on one wedged tab, `fetch` with `credentials:"omit"` → **401 in ms**; `fetch` with `credentials:"same-origin"` to the same route and to `GET /ui/notifications` → both stuck at `state:"start"` past 6s. The tab's OWN htmx polling also froze: `/ui/requests` resource-timing entries stayed at **1** across a 6s window. Fired within ~1s of `open`, the identical credentialed GET returned **200**. `nextHopProtocol` is `http/1.1` and the page holds `/events` SSE streams. The successful tap was made with `open --wake=300` followed immediately by the click.
- 🔴 **Ruled out:** that this is the reply route, htmx, the trusted click, or `hx-confirm` — a bare `fetch` stalls identically, and an uncredentialed POST to the same route answers instantly. `via: measurement`
- **NOT ruled out, and the reason this is not filed as a clawgate defect:** Zach's own foreground tab on the same origin polled normally throughout, logged server-side the whole time. So the stall may be an artifact of a hidden/CDP-driven tab rather than anything a phone would hit. **Two mechanisms, one observable** — do not pick between them from the stall alone.
- **Next probe:** count the tab's live SSE streams against Chrome's 6-per-host HTTP/1.1 budget (`/events` over `sse.js`, i.e. XHR-backed), and re-run the same credentialed GET in a FOREGROUND tab to see whether visibility is the variable. If a real phone tab can wedge this way, a tap silently does nothing — which would be worth a card of its own.

### `main` is red on a test nothing in this arc can reach
as-of: 2026-09-20
- **Symptom + exact repro:** `gh pr checks <any devrc PR>` → `tekton/devrc-pytests fail`, `FAILING: test_engine_is_the_version_every_measurement_is_keyed_to`.
- **Observed (with values):** at `91f1622d`: `collected=24040 passed=24035 skipped=4` ⇒ exactly **1** failure, named. Lives in `scripts/tests/test_opencode_engine.py`. `main` carries a byte-identical failure at `c46bb9d4`, `a2b1893a` and later heads; it **passes on the dev host**.
- **Ruled out:** attribution to #1795 or #1784 — neither range touches any `opencode` path, and three independent audit rounds re-derived that. `via: measurement`
- **Leading hypothesis:** an engine/version pin in `test_opencode_engine.py` went stale against the installed opencode; sandbox-tier-only.
- **Next probe:** `nix build .#checks.x86_64-linux.pytests 2>&1 | grep -A20 test_engine_is_the_version` — read the assertion's expected-vs-actual, then decide whether the pin or the environment moved.

## Next steps (ranked)
1. ~~**Close the closing condition.**~~ ✅ **DONE 2026-09-20 12:05 CDT** — a real tap on the second option of card 17800 delivered `Blackcurrant`; the six-link chain is in State now. **The arc's central claim is no longer inference.** Nothing remains on it except the scope caveat recorded there (one ask, one host, one modal shape).
2. **Cut a clawgate release so #855's scroll fix reaches the pod.** `ZacxDev/homelab-infra` `edefd679f` is on `trunk`; the image pin in `clusters/workbench/apps/clawgate/deployment.yaml` has NOT moved, so `/tmux` on the phone still runs the old code. Load the `clawgate` skill's `deploy.md` first. ⚠ That release also ships #855's behaviour change across **seven** panels.
   forcing: user — the operator reported the mobile scroll symptom and cannot confirm the fix until it is on the pod; the audit was explicit that the downward direction is covered "by argument yes, by measurement no."
3. **Unbreak `main`'s red `test_engine_is_the_version_every_measurement_is_keyed_to`.** Repo `devrc`, `scripts/tests/test_opencode_engine.py`. Diagnosis block above.
   forcing: gate — a permanently-red required-looking check trains click-through, and `main-green-check` reproduces it every 4h.
4. **The laptop carries uncommitted work that is IN its deployed artifact.** `ship.sh` reported `🔴 DIRTY AND IN THE ARTIFACT — scripts/opencode/opencode.jsonc`, and **6 dirty paths (5 tracked)** on `zach@10.42.0.100`. Its generation is `origin/main` PLUS them.
   forcing: gate — `ship.sh` prints it on every run, and unsaved work in a working tree is one routine `checkout` from silent deletion.
5. **#1794 — `session-manager` reports NOT-WAITING for a window blocked on a multiSelect ask.** `_MENU_SELECTED_RE` needs the digit immediately after the cursor, so `❯ [ ] 1. …` matches nothing; measured `{'probable': False, 'signals': []}`.
   forcing: none
6. **#857 — the panel scroll-anchor ledger reads a SAFE verdict off a body it never substitutes.** `ZacxDev/homelab-infra`; a ninth panel of that shape passes the whole suite green. Both controls already demonstrated in the issue.
   forcing: none

## Defects (batched)
- Round 5's three 🟢 non-blocking nits on the merged code, all `scaffolding`: the `sys.modules` comment at `test_tmux_reply_agent.py:5949-5951` calls a pre-seeded entry "an import-file-mismatch waiting to happen" and measured it is not, in either invocation shape; the seam guard pins that each probe pattern is still **listed**, not still **compared** (neuter the alarm's comparison to `if False` and the guard stays green); and a pure reorder reds correctly but the custom message prints two empty lists, leaving pytest's own diff to name the index.
- `MENU_CHECKBOX` omits the bundle's ASCII-fallback tick `√` (U+221A) and includes `✓` (U+2713), which the bundle never emits — **unreachable**, because the same `M4s()` switch that selects `√` also makes `pointer` `>` instead of `❯`, so nothing classifies as a menu at all. Fails safe.
- The distinct-footer ledger floor is walkable by a **swap** (drop a measured footer, add a novel one with a correct verdict) — inherent to a `>=` ratchet, present before this arc, and the header already calls it one.

## Gotchas / decisions / dead-ends
- 🔴 **The unit runs the script from the WORKING TREE, so `git pull` + `home-manager switch` does NOT deploy it — only `systemctl --user restart tmux-reply-agent` does.** `ExecStart=… python3 %h/workspace/devrc/scripts/tmux-reply-agent`. Verify the consumer, not the deploy: leaf of `/proc/<pid>/cgroup` must read `tmux-reply-agent.service` (the FIRST `.service` in that path is `user@1000.service` — reading `head -1` names the user manager, not the unit), and the file's mtime must PRECEDE the process start.
- 🔴 **`AskUserQuestion` renders a MENU, not a text prompt.** Typed characters are discarded and `Enter` takes the **highlighted** row. That is why the original code — `send-keys -l -- '<label>'` then `send-keys Enter` — delivered option 1 every time. The index is now safe *because it is read off the pane*, not assumed.
- 🔴 **Three prescriptions in this arc were measured WRONG, each caught only by leaving the fixtures.** (a) An audit reported three panes as "quoted text, zero numbered rows"; they were **live modals** the detector could not see. (b) That audit's positive control was a **synthetic** menu with tightly-packed rows, so it passed while the detector was blind to every real render. (c) A remedy named `Enter to select · Tab/Arrow keys to navigate · Esc to cancel` "the modal's footer" — it is the **CHAIN's** footer; a single ask renders `↑/↓ to navigate`, so requiring it would have **refused every single-question modal and left the feature inert**.
- 🔴 **Every pre-existing menu fixture in `test_tmux_reply_agent.py` once carried an INVENTED footer** (`↑↓ to select · enter to confirm`) that Claude Code never renders. Treat any fixture in that file as suspect until its shape is seen in a real pane.
- **The footer set is NOT closed, and the code is safe anyway — for a different reason than the enumeration.** `Enter to select` is assembled from `<chord>`/`<action>` components, so a grep for `chord:"enter",action:"select"` sees only the literal-pair path: **≥23 sites in 2.1.232 can spell the anchor, not 16** (six via `lo`'s `fallback:"Enter"`, one via the picker's defaulted action). All of them resolve `FOOTER_UNKNOWN`. **Safety rests on the GRAMMAR.** A 627-component reconstruction at four widths admitted only the two ask renderers themselves.
- **`scripts/devhost-tests/test_claude_footer_sites.py` will red on most Claude Code bumps, by design.** Measured across six bundles (1.0.128 / 2.0.75 / 2.0.76 / 2.1.4 / 2.1.9 / 2.1.232): every probe is 0 on all five older ones. What keying on counts buys is the **re-derivation instruction**, not immunity. ⚠ `gate.sh` and `flake.nix` default to `--set hermetic` so it is opt-in — **but `githooks/tests-on-push.sh` runs `--set all`**, so an installed pre-push gate would block every push until the ledger is re-derived. `core.hooksPath` measured unset local+global on 2026-09-20; that value is volatile.
- 🔴 **NO PROBE MAY BE KEYED ON A MINIFIER-GENERATED IDENTIFIER** (`lo`, `qJl`, `hfw` are build artifacts). De-minified forms give identical counts on every bundle.
- **A guard on WORDS is walkable by prose ABOUT the guard.** The seam guard substring-matched the alarm file's text; the round that documented it added the same literal to that file, taking it 1 → 2 occurrences — and three rots then survived, including **deleting the probe that finds the AskUserQuestion renderers**. Fixed by importing the module and comparing `tuple(probe[0] for …)`. Rot matrix: 3 SURVIVED at `285e25b8`, **0 of 6 at `91f1622d`**.
- **The kept cost, stated because it is real:** where the ask is gone and a *different* menu is up, a correct delivery is reported `failed` (an audit row, never a second keypress — nothing on that path retries). Deliberate; the alternative asserts a measurement made once by a unit that presses digits into live panes.
- **Refusals that go inert rather than wrong, measured:** a chain footer quoted in a pane; an ask footer with no parseable block; and **any pane narrower than ~30 columns**, where `Enter to select` wraps → `FOOTER_UNKNOWN` → every ask refused. FOOTER_SINGLE holds at 104/60/48/40; all live widths ≥79.
- **Height was the dimension nothing was measured at twice**, and it hid both round-3 blockers. Resizing real windows (not truncating captures) gave: 40–16 correct; **15/14 a chain reads `menu`**; **13–10 both read `text`**. The TUI re-renders to fit and scrolls its own option list, marking the edge `↓` where `MENU_OPTION_RE` expects whitespace — a fourth clipping route no round had named.
- **`AskUserQuestion` cannot be answered by a hook.** `PermissionRequest` is allow/deny/defer only; the Agent SDK's `canUseTool` is the sole programmatic path and requires hosting the query loop, which clawgate cannot. Verified against current docs AND asserted independently by `internal/api/server.go:1169`. Do not re-open it.
- **The TUI already has a multi-question chain with submit-at-end** (tab strip, `☐`→`☒`, left-arrow back-nav, `Review your answers` → `Submit answers`). Free text on question 1 does **not** return the call, so a chain needs N answers + submit.
- **multiSelect is a MODE of ask renderer 1**, not a separate renderer — it emits no footer of its own, so `menu_footer` returns `FOOTER_SINGLE` for a live multiSelect and `menu_checkbox_block` is the whole guard. **The footer grammar cannot distinguish a driveable ask from a multiSelect.**
- **Per-question notes have no delivery path** — the field exists in the tool schema, but with no hook path the TUI is the only writer of the answer payload and it offers no note affordance. Dropped from scope deliberately.
- ⚠ **A payload count from a `#`-and-blank filter is WRONG for Python**: docstrings are not `#`-prefixed, so a docstring-only commit reported 34 "payload" lines. The honest measure is `ast` — count statements and subtract docstring `Expr` nodes, or hash a docstring-blinded unparse.
- ⚠ **zsh ate `$sha:scripts/...`** as a history modifier (`:s`) → `bad substitution`. Brace it: `${sha}:path`.

## How to verify
```bash
# the merge, BY CONTENT (a squash is never an ancestor)
git -C /home/zach/workspace/devrc fetch origin -q
git -C /home/zach/workspace/devrc grep -c 'def menu_footer' origin/main -- scripts/tmux-reply-agent   # 3

# the deploy — the CONSUMER, not the deploy
for h in local laptop; do :; done   # workbench below; laptop via ssh zach@10.42.0.100
P=$(systemctl --user show tmux-reply-agent -p MainPID --value)
awk -F/ '{print $NF}' /proc/$P/cgroup            # tmux-reply-agent.service  (NOT head -1)
sha256sum /home/zach/workspace/devrc/scripts/tmux-reply-agent | cut -c1-16   # ac8e88ae960b18c5
python3 -c "import os;print(os.stat('/proc/'+'$P').st_mtime > os.stat('/home/zach/workspace/devrc/scripts/tmux-reply-agent').st_mtime)"  # True

# the suite
nix develop /home/zach/workspace/devrc -c python3 -m pytest \
  scripts/tests/test_tmux_reply_agent.py scripts/devhost-tests/test_claude_footer_sites.py \
  -q -p no:cacheprovider          # 201 passed

# the closing condition — RUN 2026-09-20 12:05 CDT, MET. To re-run it:
#  1. tmux new-session -d -s replyprobe -x 120 -y 40      # 79+ cols / 16-40 rows, the measured-good box
#     then launch `claude` in it and ask for a 2-option AskUserQuestion.
#     🔴 tear down with `tmux kill-pane -t <pane>` — `kill-session` on the default socket is BANNED.
#  2. HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
#     curl -sf $API/api/attention -H "Authorization: Bearer $HOOK" | jq '.[] | select(.tmuxPane=="<pane>")'
#     ⚠ /api/attention returns a bare ARRAY — `.entries[]` errors.
#  3. tap the SECOND option (see the browser gotcha below), then:
#     tmux capture-pane -p -t <pane> | grep -A2 'User answered'   # must name the option you tapped
#     journalctl --user -u tmux-reply-agent --since '5 min ago'   # must say `menu row N was sent`
```

🔴 **To drive the tap from a bridge-owned tab, the timing is the whole trick** — two windows that
barely overlap, and missing either fails SILENTLY in a different way:
```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work open 'http://192.168.50.250:30302/attention' --wake=300   # NOT plain open, NOT --wake (1500ms default)
$BB --instance work js '(function(){window.__c=[];window.confirm=function(m){window.__c.push(String(m));return true;};return "shim"})()'
$BB --instance work click '#reply-attention-<id> [data-reply-option-label="<LABEL>"]'
```
- **Too early / not woken → the click is INERT**: htmx has not bound the button, `window.confirm`
  is never called, `__c` stays `[]` and nothing reaches the network. It looks like a refused reply.
- **Too late (~4s+) → the request HANGS**: see the credentialed-stall investigation above. `net:[]`,
  no server log line, and `hx-disabled-elt` leaves the button permanently `disabled` so every
  later click is dropped at `htmx:confirm` with no confirm call — which reads exactly like the
  inert case but is not.
- **The confirm shim is REQUIRED, not a convenience.** The card carries `hx-confirm` and clawgate
  registers no `htmx:confirm` handler, so it is the NATIVE `window.confirm`. Without the shim the
  dialog blocks the renderer and every later bridge op dies `cdp_timeout:Runtime.evaluate`. A
  reload WIPES the shim — reinstall after any `nav`, and confirm `__c` is non-empty afterwards.
- Auto-accepting that dialog is the operator's "OK", not a bypass of a guard: the server-side
  check is `requireArmedTerminalUI`, and the reply still had to carry a real session cookie.
