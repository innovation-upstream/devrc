# Handoff: bridge-unbounded-waits — 2026-08-25

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Started as "verify PR civitai-developer-docs#62 deployed, then settle whether app-capture's
foregrounding machinery can be deleted". The probe refuted the premise and exposed **one
defect class — a wait whose only backstop is someone else's timeout** — found three times in
the browser-bridge and fixed at the source each time.

## State now
- **The bridge-unbounded-waits arc is CLOSED.** All three fixes merged and verified
  earlier; nothing further was needed on them. Carried forward because the shas and
  constants are the durable record:

  | PR | defect | constant | merge |
  |---|---|---|---|
  | #797 | `captureVisibleTab` can HANG, so the `catch` that promised a CDP fallthrough never ran; op died at the 18 s `EXEC_OP_BUDGET_MS` | `FAST_CAPTURE_BUDGET_MS = 1500` | `b20b78355` |
  | #814 | `open`'s `chrome.tabs.get` — same shape, the audit's predicted regeneration | `REUSE_TAB_BUDGET_MS = 2000` | `b242fc2df` |
  | #820 | test safety-nets TIGHTER than the CLI's own `curl -m 60`, at 31 sites | `CLI_TIMEOUT_S = 300` | `366de0912` |

- **talos-infra issues filed by earlier sessions:** #1288 (`app-requests` list semantics),
  #1289 (the dropped `SHARED_LIST_RESULT`, see the open investigation below), #1293.
  **#1297 is now CLOSED** — all three of its items are done.
- **#1306 MERGED** as squash `0e4fc872a` (2026-08-26): removed two RETRACTED claims that
  were still being PRINTED at runtime by `app-capture`, and closed #1297 items 2 and 3.
- 🔴 **NEW 2026-08-27: talos-infra #1316 MERGED** as squash `4379c27cf` — #1297 item 1,
  the frame-relative declared crop. Verified on `origin/trunk` **by content** (a squash
  merge never makes the branch head an ancestor, so ancestry proves nothing):
  `RECT_Y_APP_FRAME` ×8 in `frame.py`, the iframe-bottom bound present, `yFrom` in
  `app-requests.json`, `_measuredGeometry` in `playable-collections.json`, `F13d` in the
  suite, `M181` in the battery. `tekton / gitops-ci` = SUCCESS on the head SHA,
  `total_count: 1` (0 is NEVER settled in this repo). Branch left undeleted; worktree
  removed.
  - **What it added:** a THIRD crop form — `crop.rect` with `"yFrom": "appFrame"` used
    *alongside* `fromAppFrame`, anchoring only `y` to the app iframe's top edge. Shipped
    crops: `app-requests` `x=446 y=507 w=804 h=524`; `playable-collections`
    `x=306 y=0 w=1084 h=573`. Both re-run end to end with `--render`, both states clean,
    store-bounds green. **Nothing was attached to any listing.**
  - Suite **142 PASS / 0 FAIL**; mutants M160–M181 (M176 retired with its reason
    recorded), all killed by their own declared gate.
- **NINE audit rounds ran on #1316; round 9 was clean and ended the ladder.** The
  production code has been unchanged since round 4 — every finding after that was a
  claim in prose or coverage bookkeeping wider than the code. Details in Gotchas.
- **IN FLIGHT: nothing of ours.** No open PR, no half-written branch, no worktree.
- 🔴 **`--repo devrc` primary clone is on `main`** with two pre-existing untracked/deleted
  files that are **not ours** (`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
  and a deletion of `claudedocs/handoff-discord-embed-ext-rescue.md`). Do not "rescue" or
  restore either.

## Open investigations — live diagnosis state

### What starves the in-process `ThreadingHTTPServer` under devrc-pytests load
- **Symptom + exact repro:** `test_browser_cli_backs_off_on_429` fails in the
  `tekton/devrc-pytests` gate as `subprocess.TimeoutExpired` from
  `subprocess.run([...], timeout=…)` at `scripts/browser-bridge/tests/test_server.py`. Passes
  locally 5/5 in 0.70–2.17 s.
- **Observed (with values):** the failing CI run and a local build resolved to the **SAME nix
  derivation** `/nix/store/9cvfsmpjq6ip5yiv51mk8mf1f9zazpdz-devrc-pytests.drv` — built GREEN
  locally (`rc=0`), failed in the gate. Step exit codes `pytests 0, nodetests 0, verdict 1`,
  which devrc#810's classification defines as a real test failure, not a pipeline abort.
  Gate line: `FAILING: test_browser_cli_backs_off_on_429 | TOTAL collected=15827
  passed=15824 skipped=2 failed=1`. Target took **295.74 s** in CI vs ~230–250 s locally.
- **Ruled out:** *infra/pipeline abort* — step codes say otherwise, and PR 812 (pending at
  the time) later PASSED both legs on the same platform. *A code defect in the branch* —
  identical derivation, opposite outcomes. *The CLI retrying* — the 429 branch
  (`browser:1214-1227`) `die`s immediately, no retry ladder, `retry_after: 1.5` ignored.
  *curl being unbounded* — FALSE, it carries `-m 60`; my earlier "0 timeout flags" was a grep
  that searched only the LONG forms.
- **Leading hypothesis:** under gate load the test's own in-process `ThreadingHTTPServer`
  thread is starved and never services the accepted connection, so curl blocks until the
  test's net fires. #820 raised the net above the CLI's own bound so the CLI's error surfaces
  instead of an opaque timeout — but the stall itself is UNFIXED and UNMEASURED.
- **Next probe:** instrument the test to record the wall time between `srv.serve_forever()`
  starting and `do_POST` entry, then run the gate under contention; if that gap is the whole
  delay, the fix is in the fixture (pre-warm / a real socket server), not in any budget.

### `playable-collections` drops a malformed `SHARED_LIST_RESULT` on every load
- **Symptom + exact repro:** `capture.sh <recipe> --evidence --no-frame` on
  `playable-collections`; read `<state>.evidence.json` → `console.messages`. Verbatim:
  `[warn] IframeTransport: dropping malformed "SHARED_LIST_RESULT" message from
  https://civitai.com` — once in `discover`, twice in `mine`, every load.
- **Observed (with values):** LATENT, not an outage — the app still renders its list
  (re-measured 2026-08-25: `ul=1, li=24, testids=107`). Sender is
  `civitai:src/components/AppBlocks/PageBlockHost.tsx:2453` (success) / `:2470` (error);
  validator is `isValidSharedListResult` in the app-sdk's `internal/validate.ts`.
- **Ruled out, by reading the code — do NOT re-derive these four:**
  (1) `nextCursor: null` — `apps-shared.router.ts:339` returns `undefined`, not null;
  (2) a Postgres bigint arriving as a string and failing `isFiniteNumber` — `count` is
  `COALESCE(c.count,0)::text` then `Number(r.count)`, `author_user_id` is an int column;
  (3) a missing `value.title` — the write path enforces `title: z.string().min(1)`;
  (4) a non-string `error` on the error path — `storageErrorMessage()` always returns a
  non-empty string.
- **Leading hypothesis:** SDK version skew — the deployed block bundles whatever
  `@civitai/app-sdk` it was built against, and an older `isValidSharedListResult` may reject a
  field the current host now sends (`viewerVoted` is the obvious candidate; the current
  validator tolerates it as additive-and-optional).
- **Next probe:** the warning logs only `data.type`, never the payload — so the highest-value
  change may just be to log the first failing FIELD. Otherwise: compare the block's bundled
  SDK version against the shape the host sends. Full write-up: talos-infra issue **#1289**.

### Does `chrome.windows.update({focused:true})` actually take the operator's screen?
- **Why it is open:** `browser activate` does THREE things and the consent gate covers
  only one. Measured by reading `<devrc>/scripts/browser-bridge/extension/service_worker.js`:
  `activate` unconditionally runs `chrome.tabs.update(tab.id,{active:true})` **and**
  `chrome.windows.update(tab.windowId,{focused:true})`. The `--focus` gate governs the
  host-side `i3-msg` ONLY.
- **Observed (with values):** `<devrc>/scripts/browser-bridge/server.py` carries the
  retraction verbatim — *"⚠ `takes nothing` (an earlier wording here) OVERSTATED it …
  the extension also calls windows.update{focused:true}, which this gate does NOT
  cover. Under i3's default `smart` … a focus request from a window on an ACTIVE
  workspace `will receive the focus`."* The bridge README goes further:
  *"whether `chrome.windows.update({focused:true})` actually emits that X11 activation
  request … nobody has measured it … unknown, not zero and not proven non-zero."*
- **Ruled out:** "activate is inert without --focus" — FALSE, and it was written into
  nine places in talos-infra before an audit caught it. The tab-activation half always
  happens, and it is what routes a capture onto the `captureVisibleTab` fast path.
- **Leading hypothesis:** the residual is real but bounded to the case where Brave is
  ALREADY on the visible workspace (cross-workspace, i3 `smart` refuses). Magnitude
  unknown in both directions.
- **Next probe:** with Brave on the CURRENTLY VISIBLE workspace and focus on another
  window, record `xdotool getactivewindow`, run `browser --instance work activate`
  (no `--focus`), re-read `xdotool getactivewindow`, and restore. 🔴 Assert the
  workspace state INSIDE the arm and abort if it does not hold — the 2026-08-24
  occlusion probe gave a confident WRONG answer twice for exactly that reason.
- 🔴 **Consequence nobody has measured, and it is the one that matters:** on the
  `--trusted` spend path `plan_spend` emits `activate` with **empty args** — no
  `--focus` — so whether the subsequent `xdotool key --clearmodifiers` reliably lands
  on Brave rests on this same residual. `plan.py` now says so instead of claiming the
  step "STEALS THE OPERATOR'S SCREEN". Behaviour deliberately unchanged.

### `sensei`'s declared `y: 97` — which side of the iframe top is it on?
- **Symptom + exact repro:** `sensei.json`'s `crop._notFromAppFrame` asserts `y: 97` is
  BELOW the iframe's top edge, clipping the app's own buzz bar. Measured live 2026-08-26
  on the same `apps/run/<slug>` shell (via `app-requests`): iframe top = **141** with the
  banner present, banner rows **68…104** (height 37), breadcrumb ends 139. So 97 is
  *inside the banner* — above the app.
- **Observed (with values):** probe answer for two other apps on that shell,
  `APPFRAME_RECT:141,64,-1,1709,1255`, from `capture.sh`'s own `$st.rect.json`. Row scan of
  `top.png` at x=w/2: page bg to 67, banner 68–104 (`#29a3bc`-ish), breadcrumb 105–139,
  page bg 140, iframe from 141. sensei's own rect is dated **2026-08-15** and was measured
  on a 1709x1255 capture.
- **Ruled out:** that the shell differs per app — all seven recipes' `url` is
  `civitai.com/apps/run/<slug>`, enumerated not sampled. That the fixtures answer it —
  they are 1709x1314 and pin 163/199, both superseded (the iframe top moved up ~58 px
  between 2026-08-22 and 2026-08-26).
- **Leading hypothesis:** the recipe's note is backwards and has been for a while; the
  shipped crop's top rows are page chrome. NOT established — the layout the rect was
  chosen on no longer exists to measure.
- **Next probe:** load `civitai.com/apps/run/sensei`, wait for the app to actually render
  (the ready gate, not a bare `--wake`), then run
  `frame.py frame-rect-js --recipe <sensei.json>` in that tab and read the top. One
  attempt on 2026-08-26 returned `APPFRAME_ABSENT` with **zero** iframes on the page after
  a 12 s wake — an observation about one un-gated load, not a diagnosis.

### 🔴 sensei's live STORE ASSET is still the pre-fix crop
- **Symptom + exact repro:** #1333 fixed the RECIPE; it attached nothing. The listing on
  civitai.com still serves the crop taken at `y: 97`, whose top ~44 rows are the rewards
  banner's tail plus the `Apps / Civitai Sensei` breadcrumb.
- **Observed (with values):** measured 2026-08-27, iframe top **141**, banner rows
  **68..104**, breadcrumb **105..138**, app header **141..203** with the operator's buzz
  chip at **161..181**, header divider **204**. The corrected crop starts at **205**.
- **Ruled out:** that this is a viewport artefact — the same shell measured 141 on two
  other apps the day before, and the bands reproduce across two independent captures.
- **Leading hypothesis:** n/a, this is not a diagnosis — it is a shipped asset that no
  longer matches its recipe.
- **Next probe / closing condition:** re-shoot sensei and attach, which is a DELIBERATE
  act nobody has authorised yet. 🔴 Before doing so, read the height-responsive caveat
  below — a re-shoot in the banner-ABSENT layout DROPS the model/temp control bar
  entirely.

### sensei is HEIGHT-RESPONSIVE and the anchored form does not fully model it
- **Symptom + exact repro:** its layout is header / flex-1 main / **bottom-anchored**
  control bar (full-width divider at abs **1133**, controls at **1157..1166**), so its
  bottom furniture tracks the iframe's LOWER edge, not its top.
- **Observed (with values):** banner-absent puts the iframe top at ~104, so the crop
  resolves to abs **168..1150** while the control bar sits at 1157..1166 — **wholly
  outside**. It is DROPPED, not clipped. 🔴 Silently: the iframe-bottom bound does not
  fire, because ending EARLY is not running past the edge. What IS clipped in that
  layout is the support widget at 1149..1189.
- **Ruled out:** using `bannershift.py` to test it — that fixture builds the banner state
  by DROPPING the rows that fall off the bottom, which slides a bottom-anchored element
  away: the inverse of what really happens.
- **Leading hypothesis:** a fixed `h` cannot track both layouts for a bottom-anchored
  app; the form would need a bottom anchor to do it properly.
- **Next probe:** shoot sensei with the banner ABSENT and re-measure. If the control bar
  is gone, either drop it from the shot deliberately or give the form a bottom anchor.

### 🔴 The devrc GATE is flaky, and item 3's five-session mystery may have been IT
- **Symptom + exact repro:** three runs of `tekton/devrc-*` on ONE unchanged commit
  (`4ea2ee71`, PR #937), retriggered by close/reopen:
  1. both legs `ERROR` — *"COULD NOT RUN: the gate stopped before this leg reported"*,
     no target URL;
  2. nodetests pass, **pytests `FAILED`** — summary `collected=17680 passed=17678
     skipped=2 failed=0`;
  3. both **pass** — summary byte-identical to (2).
- **Observed (with values):** attempt 2 reported **FAILED with `failed=0` in its own
  summary**. Locally, in the repo's own dev shell, the same tree gives
  `RESULT: PASS (exit=0)` with those same totals. `run-tests.sh` drives ~28 targets
  including non-pytest ones, so "some target exited non-zero" is plausible — but the
  status does not say which, so it cannot be told from a real test failure.
- **Ruled out:** a line-length/lint trip from #937's own diff (the file already carries
  109 lines >83 chars, max 127, and the flake defines no lint check); a genuine test
  failure (`failed=0`, and 417/417 pass locally in that file, 17678 across the suite).
- **Leading hypothesis:** 🔴 **item 3's original CI failure was this, not a stall in the
  test.** Its recorded evidence — *"the failing CI run and a local build resolved to the
  SAME nix derivation — built GREEN locally, failed in the gate"* — is the same
  signature. NOT demonstrated; better supported than the stall hypothesis now is.
- **Next probe:** filed as **devrc #943**, with a closing condition: a failing
  `tekton/devrc-*` status must name the target that exited non-zero. That one fact would
  have settled both this and the 429 question. ⚠ I could NOT read the pipeline — the
  homelab cluster is not in this machine's kubeconfig — so all of the above is from
  commit statuses plus local reproduction.

## Next steps (ranked)
🔴 **Numbering is STABLE and is half a claim's identity — do not re-rank.** Items 1,
2, 3, 4 and 7 are DONE and are kept in place rather than renumbered.

1. ✅ **DONE (#1316, squash `4379c27cf`, 2026-08-27)** — crops declared for `app-requests`
   and `playable-collections`; talos-infra #1297 closed.
2. ✅ **DONE (#1306)** — `plan.py`/`capture.sh` no longer print the two retracted claims.
3. ✅ **DONE (devrc #937, squash `7ffa4593a`, 2026-08-27)** — but NOT as this item was
   framed, and the difference is the point. The item asked for the stall to be fixed at
   its source. **Measured first, and the leading hypothesis did not survive**: the
   fixture takes 0.027–0.036 s idle and stayed under 0.10 s at 2× core load, so the
   "starved `ThreadingHTTPServer` thread" theory needs a ~10,000× change this load model
   does not produce. (Weakened, NOT refuted — the load generators are `nice -n 19`
   because this runs on the operator's desktop, so the model is deliberately gentle.)
   `browser eval` makes exactly ONE `_curl POST /cmd` bounded at `-m 60`, so one hung
   request cannot reach a 300 s net. And the test was green in CI throughout.
   So nothing was patched. The test is now SELF-DIAGNOSING instead: three seam
   timestamps reported on failure, separating "the request never reached the handler"
   from "the CLI hung AFTER the reply". Watched firing in BOTH directions with two fake
   CLIs, not merely reasoned about. **See the new investigation block below — the cause
   may never have been this test at all.**
4. ✅ **DONE (devrc PR #950)** — and, like item 3, **NOT as this item was framed**. The
   item said to bound `chrome.tabs.create` as the third instance of the #797/#814
   unbounded-await class. **Two measurements refused that remedy**, and the refusal is
   the deliverable:
   - `execute()`'s own rule forbids it: a local bound is granted only to *"an await
     inside a `try` whose `catch` implements a RECOVERY (a second, working path) … If a
     hung step has no alternative to fall through to, the choke point below is already
     the right and only answer — do NOT add a bound for it."* `chrome.tabs.create` has
     no second path, so a bound could only relabel `op_timeout:open` and leak the same
     tab 2 s earlier.
   - the harm the item NAMES — "leaks one tab per `open` while returning success" — is
     the **reuse-probe fall-through**, which needs no hang in `create` at all, and
     `server.py` had already written it down verbatim: *"deterministically ORPHANING
     the live first tab. Nothing reclaims it; only a human closes it."*

   So the orphan is now **reclaimed** — and 🔴 **WHERE that happens took three
   rounds and two wrong answers, which is the durable lesson here.** Drafts 1 and 2
   had the EXTENSION close the tab; both were wrong the same way. **Reclaiming is
   correct only if the `open` result is DELIVERED** — if it is not, the old tab is not
   an orphan at all: the ownership record still names it, it is still live, and closing
   it strands the session on a dead id and then, one op later, on the operator's ACTIVE
   tab. The extension cannot know, because **TWO parties abandon an op on TWO clocks
   and neither is visible from inside it**:
   - `execute()` RACES the op against `EXEC_OP_BUDGET_MS` and cannot cancel it, so a
     merely SLOW `chrome.tabs.create` resumes `open` after `op_timeout:open` was
     already answered. Found by a blind audit, **reproduced before fixing** (execMs 80,
     hung `tabs.get`, create at 300 ms → `tabsRemove []` at return, `[5]` 600 ms later);
   - the SUBMITTER gives up at its own `cmd_timeout`, which starts at **SUBMIT**, so
     queue time is structurally invisible extension-side (`server.py`: *"The SUBMITTER
     giving up … does NOT free the extension"*). Found by the DELTA audit of the fix
     for the first — an elapsed-time guard in the extension closed one and could not
     close the other.

   Final shape: the extension **reports** `orphanTabId` and closes nothing; the server
   closes it in `_record_ownership_locked`, which by construction runs only on a
   delivered result. That deleted the elapsed guard, its `startedAt` stamp, and a
   fixture problem along with it — **the party that knows should decide.**
   🔴 **NEW HAZARD THIS CREATED, and it is disclosed rather than fixed**: parallel
   agents can derive the SAME session id and share one owned tab (observed
   2026-07-31, recorded in `scripts/browser-bridge/reference/tabs-instances.md`). A
   re-`open` by one sibling whose probe times out now closes the tab the OTHER is
   mid-workflow on. Previously that collision was benign.
   🔴 **What is still open and deliberately out of scope**: the OTHER orphan. A
   `chrome.tabs.create` that hangs and NEVER settles is killed at
   `EXEC_OP_BUDGET_MS` while the abandoned create later produces a tab `open` never
   sees — so there is nothing to report. Closing it needs the choke point to signal
   abandonment back into the op, which it has no mechanism for. **If you pick that up,
   it is a choke-point change, not another budget constant** — re-read `execute()`'s
   narrow-exception rule first.
5. **Removing app-capture's raise** — `civitai/talos-infra`. 🔴 **PREMISE CHANGED**: the
   raise is not inert, only its i3 half is withheld. Re-scope before working it.
6. **clawgate #358** — filed earlier, picked up by a different session. Not ours; live
   state NOT re-checked, so treat as unknown rather than still-in-flight.
7. ✅ **DONE (talos-infra #1333, squash `05e3110ca`, 2026-08-27)** — `sensei`'s shipped
   crop settled and fixed. `y: 97` sat **44px ABOVE** the iframe top (141), inside the
   rewards banner. Converted to the anchored form. **Nothing was attached to any
   listing** — see the open investigation on the live asset.

## Gotchas / decisions / dead-ends
- 🔴 **`browser ping` → `buildMarker` is the ONLY field describing RUNNING code.** Both
  profiles reported `extensionVersion 0.8.1` while executing DIFFERENT builds (issue #324's
  scenario, live). A `home-manager switch` moved the deployed build mid-session, so a profile
  reloaded earlier silently became the stale one — **re-check both after any switch.**
  `brave://extensions` ↻ often no-ops (the long-poll holds the worker alive); a full Brave
  restart is the reliable path.
- 🔴 **`--ff-only` on the devrc base clone REFUSED — correctly.** The clone sits on other
  sessions' feature branches (`feat/discord-embed-enlarge`, then
  `fix/discord-embed-ext-overhaul`/PR #838). **Never point a committing tool at
  `~/workspace/devrc` — use a worktree.** One local commit there was byte-identical
  (tree `2fe66a1a4be4`) to merged PR #804, i.e. a stale orphan, not unsaved work: hash before
  treating a local commit as WIP.
- **Occlusion was never the screenshot discriminator** (retracted). The hang reproduces with
  the window on a non-visible workspace and NOTHING drawn on top. Apps also boot fine hidden
  (4/4). Record: `<datapacket-talos>/claudedocs/app-capture-occlusion-refutation-2026-08-24.md`.
- 🔴 **When a probe's arms are defined by DESKTOP STATE, assert the state INSIDE each arm.**
  Two runs gave a confident WRONG answer ("the machinery can be deleted") because the
  workspace was assumed, never read back — every `i3-msg` returned success and none was
  checked for EFFECT. Expect to contend with a live human moving the workspace under you.
- **DECISION: shrink a guard rather than harden it.** Four audit rounds on #820 each found a
  🔴 *inside the guard*, never in the substitutions that fixed the bug. Three branches
  (`Popen(...).wait()`, pre-built `cmd` list, `args=`) had ZERO corpus instances — dead code,
  unguarded (deleting each left the suite green), and their module-wide maps were scope-blind
  enough to demand a 300 s CLI budget on an `echo`. Deleted, with the limits STATED.
- 🔴 **Five claims had to be retracted this session, every one a NUMBER or a SCOPE, none
  caught by a green suite**: a grep that searched only long flags (`-m 60` missed); a regex
  using `[^)]*?` that cannot cross a `)`; a scan reading only `__file__`; a worst-case curl
  count of 3 when the repo's own test asserts 4; an invented "380 tests" (actually 1120). The
  runtime code was right nearly throughout. **This is what task 358 exists to mechanise.**
- **`RULES.md` already carries this rule** (`guards-narrower`, and it records "Six in one
  session"). Prose has now failed this class twice — #358's non-goals forbid adding a sixth
  phrasing.
- **Dead end:** a survivor census over the 52 existing mutation batteries was considered as
  #358's first mechanism and REJECTED — civitai and homelab-talos have ZERO batteries, and a
  full sweep runs for hours so it cannot gate a push.

- 🔴 **RETRACTION OF THIS DOC'S OWN EARLIER CLAIM: the foregrounding machinery is NOT
  "INERT" and `activate` does NOT "raise nothing".** Believing it cost two audit rounds.
  (The wrong wording lived in **Next steps item 5**, which is a REPLACE section, so the
  2026-08-26 update overwrote it rather than leaving it quotable — this bullet is the
  only surviving record that the doc ever said it. An earlier draft of this line said
  "the entry below", which pointed at nothing.) `browser activate` does three things and the
  `--focus` gate suppresses ONE: the host-side `i3-msg`. It still makes the tab its
  window's **active tab** (which is what routes a capture onto the `captureVisibleTab`
  fast path that hangs), and it still calls the **ungated**
  `chrome.windows.update({focused:true})`. Read "withheld", never "inert".
- 🔴 **A TEST WAS WHAT KEPT A RETRACTED CLAIM ALIVE.** talos-infra gate `G14` asserted
  the exit-12 message literally contain `OCCLUSION failure`. It had been hardened once
  to stop the message advising FOCUS — and pinned OCCLUSION in its place, i.e. the NEXT
  wrong variable. Correcting the docs could never have fixed it. **When you retract a
  mechanism, grep the TEST SUITE for it, not just the prose.**
- 🔴 **A KEYWORD GUARD ON PROSE GETS WALKED. Three measured escapes, in order:**
  (a) a negative assert on `un-cover the window` also matched the CORRECT message's
  "un-covering the window is not the fix" — a guard firing on its own negation;
  (b) narrowing it to the operative i3 command was walked by the SAME advice written as
  PROSE with no command, suite green at 138 PASS; (c) pinning the body VERBATIM was
  walked by appending one line PAST the `sed` range's end anchor, again 138 PASS.
  Cure: pin the whole normalised string **to end of output**, and keep the
  position-independent negatives alongside it.
- 🔴 **A MUTANT THAT ALSO DIES UNDER THE OLD GATE PROVES NOTHING ABOUT THE NEW ONE.**
  `M112`/`M112b`/`M112c` all replace lines INSIDE the old range, so all three died
  before the fix too — reverting the range left the battery at 0 survivors and the suite
  at 138 PASS. `M112d` is the discriminating one and was watched **both ways**: KILLED
  at HEAD, **SURVIVED** with the range reverted. Ask of any regression mutant: *would
  this have died before my change?* If yes, it is not guarding the change.
- 🔴 **PROSE ABOUT "WHAT IS NOT TRUE" IS UNUSUALLY EASY TO GET WRONG IN A NEW
  DIRECTION.** Four audit rounds, four rounds of real findings, every defect introduced
  by the round before it: the headline claim not actually delivered (it still printed as
  line `[f0]` of every run, from the function whose DOCSTRING had been rewritten to
  disown it); a brand-new falsehood in nine places ("activate is a no-op"); an absolute
  in the always-loaded SKILL.md that the bridge's own source retracts verbatim; and a
  mis-attribution on the `--trusted` spend path. **Budget for several rounds and
  re-audit the DELTA each time** — none of this was caught by a green suite or by the
  mutation battery.
- **DECISION: `gen-matrix` gets NO second state, and its `too_few_states` refusal must
  stay RED.** #1297 proposed adding one. Wrong remedy: the app has exactly one state
  that does not spend Buzz, so a synthetic second state would either duplicate
  `configure` (caught by the identical-box gate) or require spending, which the recipe's
  own `_neverList` forbids. Recorded as a `_noRender` key in the recipe.
- **Shipped KNOWN-IMPRECISE rather than silently:** whether occlusion CONTRIBUTES to the
  capture hang (the refutation established only that it is not REQUIRED — a genuinely
  occluded window was never held); whether `windows.update({focused:true})` reaches X11;
  the `--trusted` keypress targeting that now visibly rests on it; and `--help`'s
  hard-coded `sed -n '2,45p'` range, which nothing tests despite the header growing twice.
- **The primary talos-infra clone shows ~20 dirty `.claude/skills/` files — that is the
  base-clone sync HOOK, not WIP.** Verified: the working copies hash-match PRIOR
  `origin/trunk` commits (`aa368b269`, `e3d62238a`). Do not "rescue" them.

- 🔴 **NINE AUDIT ROUNDS ON #1316, AND THE PRODUCTION CODE WAS UNCHANGED AFTER ROUND 4.**
  Every finding from round 5 on was *a claim in prose or coverage bookkeeping wider than
  the code*. Two are worth carrying:
  - **Round 5 — the harm was priced from the PRODUCING site.** An audit said a walked
    recipe ships a crop with 17 rows of page furniture. Measured against the real capture
    and the real probe answer, it does not: it `REFUSE`s on its next live run. The static
    gate is a drift check; `declared_box`'s live bound is the safety property, because it
    bounds by the edge THE PROBE REPORTED rather than any number a recipe wrote down.
    Three rounds of hardening the static gate were three rounds on the wrong object.
  - **Rounds 3–4 — adding witnesses to a SELF-WITNESSING gate closes nothing.** A record
    the author writes, checked against another record the same author writes in the same
    file, raises the price of a quiet edit from two numbers to three. No static gate can
    check provenance. Stop adding witnesses; state the scope instead.
- 🔴 **A COVERAGE LEDGER IS A CLAIM LIKE ANY OTHER, and "unreachable" is the dangerous
  word.** #1316's own ledger comment (a) got which-mutant-pins-which-branch wrong once,
  (b) then claimed completeness *inside the comment written to prevent completeness
  claims*, and (c) excused a guard as needing "a fixture this suite does not have" one
  paragraph from the counterexample — it took one `sed`. Each entry is now verified by
  neutering ONLY that branch and confirming its mutant SURVIVES.
- 🔴 **AN ATTRIBUTION PROBE MUST ISOLATE THE GUARD'S REPORT AND KEEP ITS `continue`.**
  Deleting a whole `if …: … continue` block removes control flow the code below depends
  on, so the mutant dies of a `KeyError` and the ledger line reads as broken when it is
  correct. This is the repo's isolate-the-mutation rule, hit *inside* the comment that
  tells you to run the check.
- 🔴 **NEVER RUN TWO MUTATION BATTERIES AT ONCE IN talos-infra.** Measured this session:
  64 gates exited **127** and the battery's self-test reported the inert-edit control as
  MISATTRIBUTED rather than scoring it. `python3` here resolves through direnv to a shared
  clone's `.venv`, which went away mid-run. Seen **three times** on this PR. Widespread
  `127` is the instrument, not the code — re-run serially before believing anything.
- 🔴 **The battery's backtick self-test fired THREE times on #1316, every time on a mutant
  label I had just written, every time before any mutant ran.** An unescaped backtick in
  an `apply_mutant` label is a command substitution. Three for three; never once the code.
- **A mutant that drops a `%`-format specifier is a FAKE KILL that `py_compile` cannot
  see.** M168's first form removed a `%r`, leaving the args tuple one long — `%` raises at
  runtime and **every** gate in that family goes red (6 FAIL), measuring the crash, not
  the message. Keep the specifier count when mutating a format string.
- **A fixture must be RE-DERIVED when a bound moves.** Adding the iframe-bottom bound
  immediately broke F13's "legal in one layout, refused in the other" case, whose numbers
  were chosen against the *frame* height. It stopped discriminating rather than failing
  loudly — the suite caught it, not me.
- **The host page's top stack MOVED ~58 px between 2026-08-22 and 2026-08-26** (iframe top
  199→141 with the banner). `frame.py`'s header windows (163/199) are a property of the
  FIXTURES and are no longer current live. Do not "correct" a recipe to them. Note the
  measured quantity is the iframe TOP — where in the stack the height went was never
  measured, and the breadcrumb sits BELOW the banner, so it could be either side.
- **`playable-collections`' `mine` state is a MECHANISM TEST, not a shippable asset.**
  Re-measured populated 2026-08-26 (2 private collections, one with 0 items) — but it
  photographs the operator's own account, which the shelf-life rules forbid.

- 🔴 **A NOTE THAT RATIONALISES A NUMBER OUTLIVES THE NUMBER, AND THEN GETS INHERITED.**
  `sensei.json` said `y: 97` is "BELOW the iframe's top on purpose, because it also clips
  the app's OWN buzz bar". Measured: 97 was 44px ABOVE the iframe, inside the banner, so
  it clipped nothing of the app's. The INTENT was right — the buzz chip is real and must
  not be shot — and only the number was wrong. But the note read as a measurement, so
  #1316 quoted it into `frame.py`, `SKILL.md` and the reference doc as the reason sensei
  "takes the trade" and "cannot adopt the anchored form". One unverified sentence became
  four confident ones. **Read a rationale as a hypothesis, especially when it explains a
  number you did not take.**
- 🔴 **CHANGING A RECIPE CAN TURN A NEGATIVE TEST VACUOUS — CHECK WHAT BORROWS IT.**
  Gate F10d built its "rect AND fromAppFrame is refused" case by borrowing `sensei.json`
  and adding `fromAppFrame`. Once sensei carried `yFrom` that is the LEGAL combination,
  so the negative no longer built the shape it is named for. (It would have failed loudly
  rather than passed — I first wrote "vacuous pass" and an audit measured it: the old
  construction gives `REFUSE[crop_rect_outside]` and `expect_refuse` checks the CODE.
  The rewrite was still right; the framing was not.) **Grep the suite for a recipe's name
  before changing its shape.**
- 🔴 **I ADDED A THIRD ADOPTER WITHOUT RUNNING THE CHECK I HAD JUST WRITTEN.** The doc
  said "both adopters are plain top-aligned scrolling lists. Check that before adding a
  third." sensei is bottom-anchored. The check was mine, one screen above the edit, and
  an auditor had to run it. **A caveat you wrote does not read itself.**
- 🔴 **THE SHARED `.venv` INTERPRETER VANISHES MID-RUN, in at least THREE shapes**: exit
  `127` across many gates, `ModuleNotFoundError`, and a truncated subprocess traceback
  inside one gate (F11). Four occurrences across #1316 and #1333. Every one looked like a
  code failure and none was; every one was settled by re-running. **Never read a
  widespread or bizarre gate failure here without re-running it first**, and never run
  two mutation batteries at once.
- **The obvious fix for the support widget makes the shot worse.** `w: 1639` (the
  recipe's own `right: 70` band) drops the floating support widget — and clips the app's
  own Send button to a bare "S". Measured: Send occupies columns **1609..1690** at
  y1078..1118, the widget **1643..1690** at y1149..1189. Both right edges are 1690, so no
  width separates them; and they are in different rows, so the only height that drops the
  widget (ending 1148) also drops the control bar. Kept at 1694, trade recorded.
- 🔴 **A BARE `wake` IS NOT A BOOTED APP, and the difference decided this item.** The
  first attempt to probe sensei (2026-08-26) opened the page, waited 12 s with
  `--wake`, and got `APPFRAME_ABSENT` with **zero** iframes — which reads exactly like
  "the app has no iframe" and nearly closed the question the wrong way. It was one
  un-gated load. Driving the same page through `capture.sh` with the recipe's OWN ready
  gate produced the iframe and the probe answer immediately. **An absence observed
  without the ready gate is not evidence of absence.**
- **A store crop's framing changes when its height does, and it is worth stating.**
  `render` fits-inside then pads: 1694x1090 gave 1200x772 (3px letterbox); 1694x982 gives
  1200x696 — 41px top and bottom, 10.5% of the canvas.

- 🔴 **A RANKED QUEUE UPDATED BEFORE THE WORK LANDS IS A STALE QUEUE.** This doc was
  updated to mark item 7 done while item 3's PR was still in review, so for ~an hour it
  told every `/resume` that item 3 was "the next item… UNFIXED and UNMEASURED" when it
  was merged. The claim lock does not cover this: the claim had been released, and the
  doc is what a session reads first. **Update the queue AFTER the merge, or in the same
  breath as it — not when the work feels finished.**
- 🔴 **A GATE CAN REPORT `FAILED` ON A LEG WHOSE OWN SUMMARY SAYS `failed=0`.** Read the
  CONTENT of a red status, not the colour: the totals and the verdict disagreed, and the
  verdict was the wrong one. Three runs of one commit gave three different answers.

## How to verify
```bash
# 1. both PRs landed, by CONTENT (squash merges — ancestry proves nothing)
git -C $DATAPACKET fetch origin trunk -q
S=.claude/skills/app-capture/scripts
git -C $DATAPACKET show origin/trunk:$S/recipes/sensei.json | grep -c yFrom            # 2
git -C $DATAPACKET show origin/trunk:$S/recipes/app-requests.json | grep -c yFrom       # 2
git -C $DATAPACKET show origin/trunk:tests/mutants-app-capture.sh | grep -c M182        # 2
git -C $DATAPACKET show origin/trunk:tests/run-tests-app-capture.sh | grep -c '"sensei": "appFrame"'  # 1

# 2. suite + doc-rot, from a clean worktree at trunk
WT=/tmp/wt-verify
git -C $DATAPACKET worktree add --detach "$WT" origin/trunk
(cd "$WT" && bash tests/run-tests-app-capture.sh | tail -3)          # 142 PASS / 0 FAIL
(cd "$WT" && bash scripts/validate-skill-paths.sh --all | tail -2)   # 1535 refs, PASS
(cd "$WT" && MUTANTS_ONLY="M179 M182" bash tests/mutants-app-capture.sh)  # both KILLED
git -C $DATAPACKET worktree remove --force "$WT"
# 🔴 ~4 mutants per run MAX, and NEVER two batteries at once — see Gotchas
```
