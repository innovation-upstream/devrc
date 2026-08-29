# Handoff: browser-agent-site-notes — 2026-08-27

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
Trace and analyse the browser-bridge skill and its flows; document the one structural
gap that trace found — the autonomous `browser agent` is BLIND to `site_notes`, so on a
registered host it runs without that site's flow notes.

## State now

- **Branch / PR:** `devrc` `main`. Two PRs merged this session, both verified BY CONTENT
  (ancestry is false forever after a squash):
  - **#980** squash **`f93cdc08`** — the SKILL.md prune (rank 3).
  - **#988** squash **`a8849cfe`** — per-build extension version. **Merged, deployed via
    `home-manager switch`, and LIVE-VERIFIED on both Brave profiles.**
  Worktrees removed, base clone fast-forwarded, no `claim-work` claims held.
- **RANK 2 IS CLOSED — and it is the first time both profiles have been verified
  EXECUTING the deployed build, not merely registered against it.** `ping` returns the
  marker frozen into each worker's own module graph:
  `work` and `personal` both `pong=True buildMarker=aada672ff3a5ded7
  version=0.8.1.43738 ops=18`, `extension_stale: false` on both.
- **RANK 3 IS CLOSED.** `SKILL.md` 12,028 B → **11,720 B**. Growth room above the 12,038 B
  enforced gate went from **10 B (0.05 ops rows) to 318 B (~1.7 rows)**. The prune also
  fixed a wrong claim the byte audit could not see: the sites row said the CLI names
  `site_notes` "in every result envelope" — `_annotate_site_notes` is called only from
  `_handle_cmd` (`server.py:3731`), never from `_whoami`/`do_GET`, and only for a
  REGISTERED host, so identity reads and unknown hosts carry no field and ABSENCE means
  nothing.
- **NEW MECHANISM SHIPPED (#988): `manifest.json` version is now `<release>.<build>`** —
  currently **`0.8.1.43738`**, where the 4th component is `int(BUILD_MARKER[:4], 16)`.
  The first three components stay human-owned. `gen-build-marker.py` regenerates both and
  `--check` verifies both. Rationale, the cycle it had to break, and the exact
  regenerate/verify commands: `scripts/browser-bridge/extension/README.md` § *Versioning*.
  🔴 **`extension_stale` is UNCHANGED and still marker-only for the all-clear.** The
  version is a human convenience, not fail-closed.
- **Two out-of-scope defects FILED rather than absorbed:** devrc **#1017**
  (`test_bash_guard.py`'s 2.0 s wall-clock assertion goes red under load — measured
  2.44–5.40 s) and devrc **#1018** (`audit-dispatch.py`'s cumulative ledger disagrees
  with the sum of its own per-round figures — 381 printed vs 466 re-derived).
- **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** (0 tasks for
  this session). Its positive control confirms the board is reachable and the token
  accepted, but a wrong session id ALSO answers 200 with an empty array — a narrow
  reading, NOT a clean bill of health. No field written, no task created.

## Open investigations — live diagnosis state

### `tekton/devrc-pytests` red on devrc — genuine failure or preemption kill? UNRESOLVED
- **Symptom + exact repro:** on PR #940 head `9e0ac29e`,
  `tekton/devrc-pytests` = `failure`, `tekton/devrc-nodetests` = `success`.
  `gh api repos/innovation-upstream/devrc/commits/9e0ac29eae6dabacb14f38beb0242b792242de5a/status`
- **Observed (with values):**
  - The same pipeline reported **success** on this PR's four earlier commits
    (`d9933e9e`, `c2576b8e`, `24a90318`, `ea1801de`), so the pipeline runs.
  - Prior run duration: pending `22:31:43Z` → success `22:52:44Z` = **21 min**.
  - GitHub status carries **`target_url: null`** — no link to the run.
  - **Locally the whole repo-wide suite is GREEN.** `nix develop <wt> --command bash
    scripts/run-tests.sh <wt>`, per-target: `scripts/tests` 1005 passed, dl-router 1005,
    **browser-bridge 792**, validation 102, session-analysis 490, session_insight 57,
    mail-actions 135, signal 716+1skip, initiatives 784, repo-cos 330+1skip. The run
    ended `RESULT: FAIL (exit=143)` at `scripts/task-spec-drafter/tests` **because I
    killed it during cleanup** — 143 is `trap 'exit 143' TERM` in `run-tests.sh:174`.
  - **No test has ever been observed red locally**, on any target, in any run.
- **Ruled out:**
  - *"Local repro of the CI failure"* — killed by my own `timeout 1500`, not a test
    (`RESULT: FAIL (exit=143)`; 143 = 128+15 = SIGTERM).
  - *"main is red"* — control on pristine `main` never ran: `run-tests.sh` printed
    "MISSING ENVIRONMENT, not a code failure — no test has run yet" and named the fix
    (`nix develop`). Its own guard, working.
  - *"Timing shows a hang"* — measured from inside a burst I caused: **load average
    22.7**, 4 concurrent pytest + 4 `run-tests.sh`. Any flake/timing read from that
    window is contaminated.
- **Leading hypothesis:** none, deliberately. The tekton skill records that this
  pipeline's failures split ~evenly between **genuine single-test failures**
  (~1 test in ~15,500, surfacing as `verdict exit=1`) and **kills** (exit 255 =
  scheduler preemption). They need opposite responses and an empty result cannot
  distinguish them.
- **Next probe:** read the TaskRun and apply the skill's discriminator — a step that
  printed `RESULT:` / `<leg> verdict=` **failed a test**; one that printed neither was
  **killed**. 🔴 **Requires the workbench** — `$KC_HOMELAB` is unset on the laptop and
  `~/workspace/homelab-talos/homelab-kubeconfig` does not exist there, so kubectl falls
  back to `localhost:8080`/`0.0.0.0:44123` and every read dies `connection refused`.
  ```bash
  KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns -l ci.zacx.dev/repo=devrc \
    --sort-by=.metadata.creationTimestamp \
    -o custom-columns='NAME:.metadata.name,SHA:.metadata.labels.ci\.zacx\.dev/sha,REASON:.status.conditions[0].reason'
  # then, for the run whose SHA is 9e0ac29e:
  KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci logs <taskrun-pod> -c step-pytests | tail -40
  ```
  Cheaper alternative from any host: the **next** devrc PR. Red on an unrelated diff ⇒
  kill signature (noise). Green ⇒ #940's failure was real and is now on `main`.

### RESOLVED 2026-08-27 — `tekton/devrc-pytests` red on #940 was NOISE, not a real failure
🔴 **This supersedes the UNRESOLVED block above. That block's evidence stands; only its
verdict is now settled.** Recorded rather than deleted so the next reader sees the prior
reading was *corrected*, not that it never existed.
- **The discriminator that settled it, and it cost nothing:** PR **#956** branches off
  `main` **including #940's squash** and adds exactly **one markdown file**. A docs-only
  diff cannot break pytest, so its check result is a statement about the TREE, not the
  diff. It came back **`tekton/devrc-pytests: success` + `devrc-nodetests: success`** and
  merged cleanly with no bypass.
- **Therefore:** the tree containing #940 passes `devrc-pytests`. There is **no persistent
  test failure from #940 on `main`** — which was the consequential half of the question.
- 🔴 **Scope this claim honestly.** It proves the tree is green NOW. It does **not** prove
  #940's specific run died of preemption — a genuinely flaky test would produce the same
  pair of readings. What is ruled out is the outcome that mattered: a real regression
  landed and left on `main`.
- **Never needed:** the workbench `kubectl` probe into the TaskRun log. Left in the block
  above because it remains the right move for the NEXT unexplained red — and note the
  cheaper move that worked here: **let the next PR's check answer it.**
- **Cost of the wrong instinct, for the record:** the local repo-wide suite was run three
  times chasing this. Every target was green every time (browser-bridge 792, dl-router
  1005, signal 716, initiatives 784, …); all three runs ended in `RESULT: FAIL (exit=143)`
  — SIGTERM — **twice from my own `timeout`, once from my own cleanup `kill`**. Not one
  test was ever observed red locally.

### CORROBORATED 2026-08-27 — rank 1's resolution now rests on TWO independent green runs
🔴 **Strengthens, does not change, the RESOLVED verdict above.** Recorded because the
resolution was originally closed on a SINGLE data point and said so.
- **Second data point:** **#957** (the rank-1 closing commit, also docs-only) branched off
  a tree containing #940 and came back **`tekton/devrc-pytests: success` +
  `devrc-nodetests: success`**, merging cleanly. So two independent docs-only PRs on
  trees containing #940 both passed.
- **What that does and does not buy.** It further rules out a persistent regression from
  #940 on `main`. It still does **not** prove #940's own run died of preemption rather
  than a flaky test — two greens are consistent with both. The claim to carry forward is
  the narrow one: **nothing broken from #940 is sitting on `main`.**

### A reused browser-bridge tab stops rendering the vetr SPA (root cause UNKNOWN)
- **Symptom + exact repro:** after ~8 `nav`s in one bridge-owned tab, every vetr route
  renders nothing — app shell present, `textContent` only the react-router bootstrap
  script. Reproduced on `/choose-experience` (logged-OUT, 4 green Playwright tests
  against the same origin), `/user`, `/user/appointments`; 6+ attempts, `--wake` up to
  10s, `localStorage.clear()`. **A `close` + fresh `open` of the SAME url renders
  everything immediately.**
- **Observed (with values):** stalled tab shows `HydrateFallback`
  (`vetr-app/app/root.tsx:377-398`) — `data-testid="app-boot-skeleton"`,
  `aria-hidden="true"`, `className="relative flex h-dvh flex-col"`, **exactly 3
  children**, no text nodes. That is the pre-hydration paint, i.e. the JS bundle never
  executed. On a fresh tab the same URLs return
  `{boot:false, splash:true, toast:false, z9999:["Vetr"]}` then the full authed
  dashboard (1232 chars) with the complete query fan-out.
- **Ruled out** (do NOT re-run these): **server** — all paths return an identical
  5176-byte SPA-fallback `index.html`, 200, `cmp` clean · **throttling** — `activate`d
  the tab, `visibilityState: visible`, animations advancing, still stalled · **seeded
  keys** — `localStorage.clear()` reproduces · **the token** — verified server-side ·
  **GTM/Brave Shields** — `TagManager.initialize` is try/caught non-fatal
  (`root.tsx:288`) · **the error boundary** — renders a visible "Oops!" (`root.tsx:400`)
  · **the brand splash** — it renders the literal text "Vetr" and these reads had zero
  `innerText`.
- **Leading hypothesis:** none worth defending. It is a bridge/tab-lifecycle issue, not
  a vetr defect — Playwright renders these routes green against the same origin.
- **Next probe:** on the next occurrence, before closing the tab, capture
  `performance.getEntriesByType("resource").filter(r=>/\.js$/.test(r.name))` and compare
  the module-chunk set against a working tab's. That distinguishes "chunk never
  requested" from "requested and never executed", which is the fork nothing so far has
  separated.

### The toast branch of the vetr overlay discriminator is UNMEASURED
- **Symptom + exact repro:** n/a — a coverage gap, not a bug.
- **Observed:** the *negative* half is measured — with no toast live,
  `.Toastify__toast-container` is absent and the always-mounted wrapper is a
  `<section class="Toastify">`, so it never enters the snippet's `div` filter.
- **Ruled out:** triggering it via the axios interceptor — both interceptor toasts are
  gated on `config?.toastr`, and vetr-api's `GET /config` sends no such key (0 matches
  across `vetr-api/{app,config,routes}` with a positive control that fires).
- **Leading hypothesis:** the library renders the container div only while ≥1 toast is
  live, making presence a binary answer.
- **Next probe:** in Lane A, click any action handler that raises a toast (~14 non-test
  files call `toast.*`), then re-run the discriminator and add the row to
  `vetr.com.md`'s results table.

## Next steps (ranked)

🔴 Ranks keep their numbers so any live `claim-work` slug still resolves.

1. ✅ **RESOLVED 2026-08-27 — the `devrc-pytests` red.** Unchanged; nothing to do.
2. ✅ **CLOSED 2026-08-29 — extension reloaded and LIVE-VERIFIED in both profiles.**
   Both execute `aada672ff3a5ded7` / `0.8.1.43738`, `extension_stale: false`, `ping`
   answering. 🔴 **Read the reload-recipe gotcha below before ever doing this again** —
   the recipe as written in the old rank 2 is INCOMPLETE and produces a profile that
   comes back ABSENT rather than stale.
3. ✅ **CLOSED 2026-08-29 — #980 merged.** 318 B of growth room, up from 10 B.
4. **Optional: round 5 delta audit of the merged #940** (repo: devrc;
   `scripts/browser-bridge/reference/agent.md`). Untouched this session. Closes when a
   round returns no findings, or an operator dismisses it in writing.
5. **Measure the toast row of the vetr overlay discriminator** (repo: devrc; file
   `scripts/browser-bridge/reference/sites/vetr.com.md`). 🔴 **NEWLY UNBLOCKED** — this
   needed a working extension, which is now true for the first time. Recipe is in the
   "Open investigations" block above. Closes when the results table carries a
   toast-present row.
6. **Root-cause the tired-tab stall** (repo: devrc, `scripts/browser-bridge/`). Untouched.
   See the investigation block for the six eliminations already paid for and the one probe
   that separates the remaining fork. Closes when the mechanism is named in
   `reference/spa-wake.md`, or an operator dismisses it in writing.
7. ✅ **Closed as already decided** — operator ruled 2026-08-28 that the `vetr.com`
   disclosure is not severe. Listed so it is not re-raised.

## Gotchas / decisions / dead-ends

- 🔴 **The `site_notes` gap itself.** `server.py` `_annotate_site_notes` sets the field on
  the **envelope ROOT** (`result["site_notes"] = path`); the agent tool's
  `summarizeResult` reads `envelope.data`. Measured across all ops the agent can reach:
  **none forwards it.** Kept deliberately — the value is a repo-relative *path* and the
  agent def denies `read` (`opencode/browser-agent.md`: `"*": deny` / `browser: allow`),
  so forwarding it hands the model a filename it cannot open.
- 🔴 **`whoami` is the exception to the `.data` mechanism** — it reads the root
  (`const w = envelope || {}`). Safe for a different reason: it answers `GET /whoami`,
  which `_annotate_site_notes` never touches. Do not generalise `.data` to it.
- 🔴 **`OP_TO_SERVER` KEYS, not VALUES.** It maps tool-facing → wire name
  (`html` → `getHtml`) and `summarizeResult` takes the tool-facing one. Iterating values
  feeds it `getHtml`, which matches no branch and falls through to the terminal
  `JSON.stringify(data)` — exercising a path the agent never takes while skipping the
  `html` branch. Round 1 also proved `ALLOWED_OPS_DEFAULT` (13) is the wrong inventory:
  `upload` is reachable via `BROWSER_AGENT_ALLOWED_OPS`, and a `site_notes` forward added
  to that branch passed the FULL 91-test suite.
- 🔴 **The detection recipe took two rounds to get right, and the middle version was
  WORSE than the bug it fixed.** `open <url> && context` reported the OLD host (a
  re-`open` discards the url). Fixing it with a *conditional* `open` was worse: `nav` is
  in `TAB_SCOPED_OPS` but NOT `OWNED_TAB_ONLY_OPS`, so with no owned tab it drives the
  **operator's ACTIVE tab** (`server.py:215-217` says exactly that). Final form:
  `open && nav <url>`, read `site_notes` off the **nav** envelope (`nav` returns
  `url: cmd.url` at `service_worker.js:960,968` — deterministic; `context` reads the
  *committed* `tab.url` and races). And `open`'s own envelope carries a **decoy**
  `site_notes` for the tab's current host — discard it.
- ⚠ **Instruments lied four separate times this session; budget for it.**
  (1) a background task reported **exit 0** while its own output said
  `RESULT: FAIL (exit=143)`; (2) a "completed exit 0" notification was the *wrapper that
  launched `nohup`*, not the run; (3) two mutation mutants **never applied** (anchor
  matched 3 and 2 sites, python aborted) and their unmutated runs read as *survived* —
  a mutant that never ran reports SURVIVED; (4) a `/whoami`-doesn't-annotate "finding"
  was my grep window, not the code (`annotate_staleness` is called inside `_whoami()`,
  `server.py:3138`). Read CONTENT, never an exit code; verify a mutation applied.
- **Merged through a red required check by explicit operator instruction.** Minimal lever
  used (`enforce_admins` only, not deleting required checks), restored and verified in
  the same command. This is recorded, not endorsed: nobody knows yet whether a real
  failure landed on `main` — that is next step 1.
- **No `clawgate-task:` field on this doc.** `clawgate_handoff.sh resolve` exited **5**
  (0 tasks for this session). Its positive control confirmed the board is reachable and
  the token accepted (3 links for a different session), but a wrong session id also
  answers `200` with an empty array — so this is a narrow reading, NOT a clean bill of
  health. Per the tool's instruction: no field written, no task created.

- 🔴 **The `open`-reuse-probe orphan named in the gotchas above is BEING FIXED — do not
  re-derive it.** `devrc#950` (**OPEN** as of 2026-08-27, *"reap the tab `open`'s bounded
  reuse probe orphans — and NOT by bounding tabs.create"*) touches
  `extension/{protocol.js,service_worker.js,build_id.js}`, `server.py`,
  `reference/tabs-instances.md` and three test files. Read it before writing anything new
  about `REUSE_TAB_BUDGET_MS` or orphaned tabs — and note it changes the extension build
  marker, which is why rank 2's hex values are a snapshot.
- ⚠ **A watcher timeout and a failing check look alike — say which you got.** A poll loop
  that exits on its own budget prints `checks are 'pending'`, one word away from a red.
  With runs at 21–26 min, a 30-min budget is not comfortable margin.
- ⚠ **`gh pr merge --auto` is unavailable on this repo** (`Auto merge is not allowed for
  this repository`), so merge-when-green must be a watcher, not a GitHub feature.

- 🔴 **Eight audit rounds on #966, and EVERY round found a real defect introduced by the
  previous round's fix.** All were the same class: *a confidently-stated wrong mechanism
  or over-broad absolute*, always in newly-added explanatory prose, never in the
  citations. Round 3 was strictly a regression — it replaced a correct "unexplained"
  with a wrong explanation. Severity decayed monotonically (round 1: a false safety
  claim about real money; round 8: an unbounded "wait it out").
- 🔴 **THE fix that broke the cycle: run the instrument.** Seven rounds *reasoned* about
  what a DOM query returns and produced seven false claims. The round that **ran** it
  produced zero, and correctly retracted a prior round for a reason no source-reading
  reaches: **Chromium clamps an out-of-range `z-index` to the 32-bit max.** Measured on
  a live page — `"999999999999"` → `"2147483647"`, which `/9999/` does NOT match. The
  source says one number, the browser says another. **When an exclusion depends on what
  an instrument COMPUTES rather than what the source SAYS, run the instrument.**
- 🔴 **An instrument change invalidates the exclusions derived under the old
  instrument.** Rounds 1–6 established "the splash is `pointer-events-none`, so a
  hit-test never returns it" — true. Round 6 swapped to a computed-style DOM query,
  where `pointer-events` is irrelevant, and carried the exclusion across unchanged. The
  splash matched, its text is "Vetr", and it fell through to "a different modal".
- **The one edit shape that never introduced a defect in 8 rounds: a pure NARROWING**
  (adding a qualification to an absolute). That is why the ladder stopped at round 8
  rather than running a 9th to confirm two narrowings.
- **Withdrawn mid-session:** an early finding that "every non-root vetr route renders
  blank in a bridge tab" and that the drive lane was unusable. It was a tired tab. The
  drive lane works — fresh tab, seed, second `nav` lands the full authed dashboard.
- **`innerText` / button-counts / `elementFromPoint` are all bad blankness detectors**
  and each gave a confident wrong answer here: `innerText` needs layout and returns `""`
  on an occluded tab; the chooser renders three role cards and counts 0 `button,a`;
  `elementFromPoint` returns `null` for an OFF-VIEWPORT point, identical to "covered"
  (vetr's off-canvas menu sits at `x = -249`). The last one is now generic, in
  `reference/css-hit-test.md`.
- **Registry routing is HOST-based, so the hermetic lane never surfaces these notes.**
  `127.0.0.1`/`localhost` resolve to nothing — i.e. the only lane allowed to click is
  the lane that will never hand you the file. Load it deliberately; and the autonomous
  `browser agent` never sees `site_notes` at all (the #940 gap).
- **`e2e:mint-tokens` REVOKES every prior `e2e` token** (`E2EMintTokens.php:77`), so a
  re-mint 401s the tab you already seeded and it reads logged-OUT.
- **Laptop `~/.config/vetr/authnet.env` holds PROD creds** while the harness forces
  `ANET_ENDPOINT=sandbox` → `e2e:mint-tokens` fails the card provision with `E00007`
  and leaves `owner_has_saved_card:false`, silently self-skipping the saved-card specs.

- **CARRIED FORWARD from the 2026-08-28 `State now` (it was measured evidence sitting
  under a REPLACE heading, one update from deletion): vetr E2E coverage.** 28 spec files /
  55 static `test()` calls in `vetr-app/e2e/`, plus two sibling harnesses
  (`tests/e2e/a11y-gate/` ratchet, `tests/e2e/ux-audit/` 15-view walk). Coverage is
  **entirely env-dependent**: ~25 passed / 35 skipped bare, vs **53 passed / 7 skipped /
  0 failed** under the hermetic stack (10.7 min). Nothing runs it automatically — Actions
  is quota-dark org-wide and Tekton runs only `vitest`/`typecheck`/`a11y` +
  `vetr-api-pest`.
- **CARRIED FORWARD, same reason — the durable half of the old rank 2:** the stale builds
  predated 2026-08-24 and were missing `b20b7835` (#797) and `b242fc2d` (#814), both
  bounded-hang fixes, **so the symptom of a stale extension is a WEDGED OP, not an
  error.** Both profiles reported the same version while running different builds, which
  is exactly the case a version compare could not see — and is what #988 now fixes.
  (Superseded as a *reading*: both profiles are current as of 2026-08-29.)
- **devrc#966 merged 2026-08-28** (squash `9bc7f5eb`) — the site-notes registry's first
  new entry since #940: `reference/sites/vetr.com.md` + its `_index.json` key, plus the
  generic `elementFromPoint` off-viewport trap relocated into `reference/css-hit-test.md`.
- 🔴 **THE RELOAD RECIPE IS INCOMPLETE, AND THE FAILURE IS SILENT.** Rank 2 said
  "`brave://extensions` → Remove → Load unpacked" and stopped. **Remove wipes
  `chrome.storage.local`** (`reference/errors.md:262`), which holds the bearer token,
  port and label — so the reloaded extension cannot authenticate and **never appears in
  the registry at all**. Measured this session: after reloading both profiles, `whoami`
  reported `connected: 1` with `personal` simply ABSENT — not stale, not erroring.
  **An absent instance reads as "that Brave window is closed", which is the wrong
  diagnosis.** The recipe must end: *"…then re-paste token/port/label in Options"*
  (token at `~/.config/browser-bridge/token`, port `8788`, label exactly `personal` or it
  registers under an auto-id).
- 🔴 **A SUBSET SUITE CANNOT FAIL ON A REPO-WIDE GUARD, and four audit rounds inherited
  that blindness.** #988 shipped a five-component version fixture whose first four parts
  parsed as a routable IPv4; devrc is PUBLIC and
  `scripts/tests/test_no_public_ips.py::test_no_unallowlisted_public_ip_literal_is_committed`
  fails the build on any committed routable IP literal. Every local run was
  `pytest scripts/browser-bridge/tests/` — "836 passed" was TRUE of a tier that could
  never collect that guard. All four audit rounds missed it because each was briefed that
  the subset was sufficient *since Tekton runs the repo-wide gate separately*, which was
  true and still left the gap. **Tekton is the tier that gates the merge; run it or say
  your green is subset-scoped.** It was then reintroduced once, because the comment
  explaining the fix QUOTED the offending literal — the gate scans comments too.
- 🔴 **FOUR INSTRUMENTS RETURNED CONFIDENT WRONG ANSWERS in this session; none announced
  itself.** (1) An `8.8.8.8` positive control for the IP gate stayed GREEN — canonical
  examples are allowlisted, so the control proved nothing; a realistic routable literal
  does turn it red. (2) A `sed` edit deleted the `START=` assignment on a compound line,
  so a check-watcher reported `settled after 1787979093s` off "no checks reported" — an
  empty result read as a verdict. (3) A zsh `set -- $spec` did NOT word-split, so a
  per-round ledger loop logged the ENTIRE history four times and printed the same number.
  (4) Two `command grep | xargs` sweeps for version-parsing consumers returned a clean
  ZERO while a known splitter existed. **Every one was caught only by a control or by a
  number that could not be true.**
- 🔴 **The audit ladder's yield was PROSE, not logic: 9 of 13 findings were a claim wider
  than its code.** Rounds ran 6 → 6 → 1 → 0 and ended clean at round 4. Round 3's single
  finding PRE-DATED the PR's own tip, so round 2 was the last round whose fixes introduced
  anything. The standout: the SAME paragraph in `reference/errors.md` named a wrong
  mechanism THREE times running — first that a branch had widened, then the wrong branch —
  and was settled only when round 3 stopped reading `annotate_staleness` and **ran** it
  with two discriminating controls isolating `:887` from `:886` and `:883-884`.
  A guard that could not fire was also shipped once, commented as "the last point before
  it is written to a manifest", while the entry point that WAS unguarded
  (`write_manifest_version`'s caller-supplied `version=`) went unnoticed *because of it*.
- **`claim-work`'s owner-id is CWD-DEPENDENT.** It is
  `hash(machine-id || realpath(git-DIR of the ident dir))` (`claim-work.sh:662`), so
  claiming from `~/workspace/devrc` and releasing from another repo makes the claim look
  like someone else's and refuses without `--force`. **Claim and release from the same
  repo dir**, or the slug strands for the full 7-day TTL.
- **`gh pr merge` prints `failed to run git: fatal: 'main' is already used by worktree`
  when a worktree holds `main` — the MERGE STILL SUCCEEDED.** That error is `gh`'s local
  post-merge checkout, not the merge. Confirm with
  `gh pr view <n> --json state,mergedAt,mergeCommit`, never from the exit path.
- **The marker deliberately does NOT hash the version value.** The version derives from
  the marker, so hashing it would make the derivation a recurrence with no fixpoint.
  Consequence worth knowing: **a release bump does not move the marker at all**, which is
  the state that genuinely reaches the version-mismatch branch at `server.py:887`.

## How to verify

```bash
# 1. both profiles EXECUTE the deployed build (not merely registered) — the real gate
for I in work personal; do printf '%-9s ' "$I"; \
  ~/workspace/devrc/scripts/browser-bridge/browser --instance $I ping; done
# expect both: pong=True buildMarker=aada672ff3a5ded7 version=0.8.1.43738

# 2. deployed tree matches the repo (read the CONSUMER, not the switch's own output)
cmp ~/.local/share/browser-bridge-ext/manifest.json \
    ~/workspace/devrc/scripts/browser-bridge/extension/manifest.json && echo identical

# 3. the version/marker pair is self-consistent; NEGATIVE CONTROL: hand-edit the 4th
#    component and this goes red naming the regen command
cd ~/workspace/devrc/scripts/browser-bridge && nix develop --command \
  python3 gen-build-marker.py --check    # expect: build marker OK + manifest version OK

# 4. the SKILL.md gate, with its own negative control (pad the file -> RECLAIM: N bytes)
cd ~/workspace/devrc/scripts/browser-bridge && nix develop --command \
  python3 -m pytest tests/test_skill_size.py -q                      # expect 4 passed

# 5. 🔴 the repo-wide tier, which the browser-bridge subset is STRUCTURALLY BLIND TO
cd ~/workspace/devrc && nix develop --command \
  python3 -m pytest scripts/tests/test_no_public_ips.py -q           # expect 15 passed
```
