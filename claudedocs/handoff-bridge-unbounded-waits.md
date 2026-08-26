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

- **talos-infra issues filed by the earlier session and still on record:** #1288
  (`app-requests` list semantics), #1289 (the dropped `SHARED_LIST_RESULT`, see the
  open investigation below), #1293, #1297 (the crop/render work — items 2 and 3 now
  closed, item 1 open; see Next steps 1).
- **NEW this session: talos-infra #1306 MERGED** as squash `0e4fc872a` (2026-08-26).
  It removed two RETRACTED claims that were still being PRINTED at runtime by
  `app-capture`, fixed the gate that was keeping one of them alive, and closed
  items 2 and 3 of talos-infra **#1297**.
  - Verified on `origin/trunk` **by content** (a squash merge never makes the branch
    head an ancestor, so ancestry proves nothing): `5/5 deadlock` = **0** occurrences,
    `STALE BRIDGE BUILD` present, `M112d` present, `TWO CAUSES` present.
  - Gate `tekton / gitops-ci` = success on the exact head SHA, `total_count: 1`.
  - Branch deleted; worktree removed.
- **Four adversarial audit rounds ran on that PR, and every round found real defects —
  each one introduced by the round before it.** The mutation battery caught none of
  them. Details in Gotchas below; this is the most transferable output of the session.
- **IN FLIGHT: nothing of ours.** No open PR, no half-written branch, no worktree.
- 🔴 **`--repo devrc` primary clone is on `main`, clean** apart from two pre-existing
  untracked files that are not ours (`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
  `scripts/dl-router/tests/load_test_store.sh`).

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

## Next steps (ranked)
🔴 **Numbering is STABLE and is half a claim's identity — do not re-rank.** Items 2
and 3 of the original list are DONE and are kept in place rather than renumbered.

1. **Declare crops for `app-requests` + `playable-collections`** — `civitai/talos-infra`,
   `.claude/skills/app-capture/scripts/{frame.py,recipes/*.json}`. **talos-infra #1297**,
   still open with a stated closing condition. Design is SETTLED and smaller than it
   looks: **the rewards-banner shift is purely VERTICAL** — measured on
   `5-mb-combinations.png` vs the `bannershift.py` cut of the same capture, `x=252`,
   `w=1200`, `h=312` are byte-identical in both layouts and only `y` moves, 194 → 230
   (exactly +36). So a declared rect needs only its **y** anchored to the iframe top,
   and the probe ALREADY reports that (`top`, in `(top, bottom, right, vw, vh)`) — no
   probe or token change. Work: make `rect` frame-relative when `fromAppFrame` is true
   (lift the exclusion for THAT FORM ONLY — an absolute rect + `fromAppFrame` must stay
   refused), gate it by measuring the same declared rect in both banner layouts and
   asserting the resolved crop is identical, then a LIVE `--render` run to pick crops
   ending on a clean row boundary rather than mid-row.
   ⚠ `playable-collections`' `mine` state was last measured populated **2026-08-16** and
   has NOT been re-measured. If it is empty now, that state shoots an empty screen.
2. ✅ **DONE (#1306)** — `plan.py`/`capture.sh` no longer print the two retracted claims.
3. **Fix the `test_browser_cli_backs_off_on_429` stall at its source** — `devrc`,
   `scripts/browser-bridge/tests/test_server.py`. UNCHANGED this session; see the
   investigation block already in this doc. #820 only stopped the test preempting the
   CLI's own timeout — the stall itself is still UNFIXED and UNMEASURED.
4. **`open`'s fallthrough shares the suspected failure mode** — `devrc`,
   `scripts/browser-bridge/extension/service_worker.js`. UNCHANGED this session.
   `chrome.tabs.create` is another unbounded browser-process IPC, so a browser-wide
   stall leaks one tab per `open` while returning success.
5. **Removing app-capture's raise** — `civitai/talos-infra`. 🔴 **THIS ITEM'S PREMISE
   CHANGED: it is no longer "removing an INERT raise".** The raise is not inert (see
   the open investigation above); only its i3 half is withheld. Removing it would
   change which capture path is taken. Re-scope before working it.
6. **clawgate #358** — filed earlier, picked up by a different session. Not ours; its
   live state was NOT re-checked this session, so treat that as unknown rather than
   as still-in-flight.

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

## How to verify
```bash
# 1. #1306 landed, by CONTENT (squash merge — ancestry proves nothing)
git -C $DATAPACKET fetch origin trunk -q
git -C $DATAPACKET show origin/trunk:.claude/skills/app-capture/scripts/plan.py   | grep -c '5/5 deadlock)"'   # 0
git -C $DATAPACKET show origin/trunk:.claude/skills/app-capture/scripts/capture.sh | grep -c 'STALE BRIDGE BUILD' # 1
git -C $DATAPACKET show origin/trunk:tests/mutants-app-capture.sh                  | grep -c 'M112d'            # 2

# 2. the suite and the gate that was walked three times
WT=/tmp/wt-verify
git -C $DATAPACKET worktree add --detach "$WT" origin/trunk
(cd "$WT" && bash tests/run-tests-app-capture.sh | tail -3)                 # 138 PASS / 0 FAIL
(cd "$WT" && MUTANTS_ONLY="M112c M112d" bash tests/mutants-app-capture.sh)  # both KILLED, by G14 alone
# 🔴 at most 2 mutants per run — 3+ exceed a 570s command timeout
git -C $DATAPACKET worktree remove --force "$WT"

# 3. M112d is DISCRIMINATING, not merely passing — the check that matters
#    revert norm_g14's range to /This is a STALE BRIDGE BUILD/,/FULL Brave restart is the reliable path/p
#    then MUTANTS_ONLY="M112d" must report SURVIVED. Restore by DIFFING the file,
#    not by a grep count (a pattern containing $ silently reports 0 on a correct file).
```
