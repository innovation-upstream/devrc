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
- **All three fixes MERGED and live.** Verified by CONTENT on `origin/main` (squash merges,
  so ancestry proves nothing) and by `browser ping` on both Brave profiles:

  | PR | defect | constant | merge |
  |---|---|---|---|
  | #797 | `captureVisibleTab` can HANG, so the `catch` that promised a CDP fallthrough never ran; op died at the 18 s `EXEC_OP_BUDGET_MS` | `FAST_CAPTURE_BUDGET_MS = 1500` | `b20b78355` |
  | #814 | `open`'s `chrome.tabs.get` — same shape, the audit's predicted regeneration | `REUSE_TAB_BUDGET_MS = 2000` | `b242fc2df` |
  | #820 | test safety-nets TIGHTER than the CLI's own `curl -m 60`, at 31 sites | `CLI_TIMEOUT_S = 300` | `366de0912` |

- **Both profiles run the new build**: `buildMarker = e1ee86a50a811d40`, `stale=False` on
  `work` and `personal - other`. 🔴 `buildMarker` is the ONLY field describing running code;
  `extensionVersion` read `0.8.1` on both throughout, including while one was stale.
- **clawgate task 358** filed (the meta-class: dead guard branches + a detector). Already
  picked up by a DIFFERENT session (`f23b37ec-…`), status `in_progress`. Not ours to work.
- **talos-infra**: app-capture SKILL.md pruned 25,817 → 13,418 B (−48%); the occlusion
  premise retracted in `SKILL.md` + `reference/foreground-and-spend.md`; the host→fixture
  map consolidated from three copies to one. Issues #1288, #1289, #1293, #1297 filed.
- **IN FLIGHT: nothing of ours.** No open PR, no half-written branch.

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

## Next steps (ranked)
1. **`--render` the three new app-capture recipes** — `civitai/talos-infra`, files
   `.claude/skills/app-capture/scripts/recipes/{app-requests,gen-matrix,playable-collections}.json`.
   BLOCKED on a design call, not on tooling: all three refuse (`app-requests` exit 5
   `full_frame` 46.9%×98.3%; `gen-matrix` exit 6 `too_few_states` — 1 state, the identical-box
   check needs ≥2; `playable-collections` exit 5, 63.9%×**100.0%**). The floors are NOT wrong —
   these apps scroll, so content legitimately fills the band. Decide declared `crop.rect`
   (as `sensei.json` already does) vs detection, and give `gen-matrix` a 2nd state. **#1297.**
2. **`plan.py`'s step text still prints two RETRACTED claims on every run** —
   `civitai/talos-infra`, `.claude/skills/app-capture/scripts/plan.py`: "an App Block does not
   boot in a hidden tab" and the occlusion story. Docs were corrected; these are code strings.
   Also `capture.sh`'s exit-12 message names OCCLUSION as the cause. **#1297.**
3. **Fix the `test_browser_cli_backs_off_on_429` stall at its source** — `devrc`,
   `scripts/browser-bridge/tests/test_server.py`. See the investigation above; #820 only
   stopped the test preempting the CLI's own timeout.
4. **`open`'s fallthrough shares the suspected failure mode** — `devrc`,
   `scripts/browser-bridge/extension/service_worker.js`. `chrome.tabs.create` is *another*
   unbounded browser-process IPC, so a browser-wide stall leaks one tab per `open` while
   returning success. Needs a live measurement of a hung `tabs.get` that nobody has.
5. **Removing app-capture's inert raise** — `civitai/talos-infra`. Optional cleanup, NOT a
   fix: `activate` without `--focus` answers `i3: "withheld"` and raises nothing. Touches
   `ACTIVATE_REASONS`, gate G11, mutants M59–M64; mutation sweeps run to HOURS.
6. 🔴 **clawgate #358 — IN FLIGHT, another session.** Do not start it.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws from it, so a
*better* ranked list produces *more* duplicate work. Items name repo + files; item 6 is
marked in flight.

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

## How to verify
```bash
# 1. all three fixes live, by CONTENT (squash merges — ancestry proves nothing)
R=~/workspace/devrc; git -C $R fetch origin -q
git -C $R show origin/main:scripts/browser-bridge/extension/protocol.js | grep -E 'FAST_CAPTURE_BUDGET_MS|REUSE_TAB_BUDGET_MS'
git -C $R show origin/main:scripts/browser-bridge/tests/cli_budget.py   | grep CLI_TIMEOUT_S

# 2. what is actually RUNNING in each profile (not what is deployed)
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work ping; $BB --instance 'personal - other' ping   # buildMarker on both
$BB health                                                         # stale=False on both

# 3. the guard #820 landed, and that it can still go red
(cd $R/scripts/browser-bridge && python3 -m pytest tests/test_server.py -q \
   -k 'test_cli_subprocess_timeouts_outrank_the_cli_own_curl_bound or test_cli_timeout_scan_sees_what_it_claims_and_only_that')
# then plant a literal timeout at a CLI site in a sibling test file -> must go RED
```
