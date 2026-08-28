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
- **Branch / PR:** `devrc` `main` @ `9bc7f5eb`, clean, in sync with `origin/main`.
  **devrc#966 MERGED** (squash `9bc7f5eb`, 2026-08-28) — verified BY CONTENT, never by
  ancestry: all three files present on `origin/main` and `vetr.com.md`'s blob OID is
  identical to the branch tip. Worktree removed; base clone fast-forwarded `--ff-only`.
- **DONE — the site-notes registry has its first new entry since #940.**
  `scripts/browser-bridge/reference/sites/vetr.com.md` (29,331 B) + the `_index.json`
  key, plus the generic `elementFromPoint` off-viewport trap relocated into
  `reference/css-hit-test.md` where every host gets it. 12 commits, 3 files, +517/−2.
  Gate green on the final head with real counts: `devrc-nodetests` 1295/1295,
  `devrc-pytests` 17824 passed / 2 skipped / 0 failed.
- **`SKILL.md` NOT touched** — re-measured **12,028 B**, unchanged, so rank 3's 10-byte
  slack is exactly as the prior doc left it. The site-notes design (SKILL.md names the
  DIRECTORY, never a site) is what let a 29 KB file ship without moving that number.
- **vetr E2E coverage traced and MEASURED**, which was the session's original ask:
  28 spec files / 55 static `test()` calls in `vetr-app/e2e/`, plus two sibling
  harnesses (`tests/e2e/a11y-gate/` ratchet, `tests/e2e/ux-audit/` 15-view walk).
  Coverage is entirely env-dependent: **~25 passed / 35 skipped bare** vs
  **53 passed / 7 skipped / 0 failed** under the hermetic stack (10.7 min). Nothing runs
  it automatically — Actions quota-dark org-wide, Tekton runs only
  `vitest`/`typecheck`/`a11y` + `vetr-api-pest`.
- **Deploy/verify status — LIVE and verified end-to-end.** Resolving against the
  INSTALLED clone (what the running bridge reads, `_SITES_DIR` is hardcoded there):
  `vetr.com`/`app.`/`api.`/`admin.vetr.com` → `reference/sites/vetr.com.md`;
  `notvetr.com`, `127.0.0.1`, `localhost` → `''`; positive control `civitai.com` still
  hits. No bridge restart was needed, as the mtime+size stamp invalidation predicts.
- 🔴 **Extension markers MOVED during this session — the prior doc's warning fired.**
  Measured 2026-08-28 at handoff time: deployed **`b817ef1e88267a40`**; `work`
  `e1ee86a50a811d40` **stale**, `personal` `04bbd6f9c695141d` **stale**. Earlier in the
  SAME session `work` read `stale: false` against a deployed `e1ee86a50a811d40`. Both
  readings were true when taken. Re-read at the moment you act; do not carry the hex.

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

🔴 Ranks 1–4 keep their numbers so any live `claim-work` slug still resolves.

1. ✅ **RESOLVED 2026-08-27 — the `devrc-pytests` red.** Unchanged; nothing to do.
2. **Reload the browser-bridge extension in BOTH Brave profiles** (repo: none —
   operator action, an agent cannot do it). 🔴 **Re-confirmed 2026-08-28 and now MORE
   stale than the prior doc recorded:** deployed marker moved to `b817ef1e88267a40`, so
   `work` (`e1ee86a50a811d40`) AND `personal` (`04bbd6f9c695141d`) both report
   `extension_stale: true`. Fix: `brave://extensions` → **Remove** → **Load unpacked**
   `~/.local/share/browser-bridge-ext/` (a ↻ reload is unreliable — the long-poll keeps
   the old worker alive). **Verify: `browser whoami` → `extension_stale: false` on both;
   `null` = undecidable, NOT ok.** Do not assert the hex values — re-read them at the
   moment you act; they moved once already inside a single session.
   🔴 **Carried forward from the 2026-08-27 entry, because it is the durable half:** the
   stale build predated 2026-08-24 and was missing `b20b7835` (#797) and `b242fc2d`
   (#814), both bounded-hang fixes — **so the symptom of a stale extension is a WEDGED
   OP, not an error.** Both profiles report version `0.8.1` while differing in build,
   which is exactly the case a version compare cannot see. The missing-commit list is
   itself a snapshot and is now a floor, since the deployed marker has moved again.
3. **Decide the SKILL.md byte budget** (repo: devrc; `scripts/browser-bridge/SKILL.md`).
   Unchanged by this session — **re-measured 12,028 B** against the 12,038 B ceiling,
   **10 bytes of slack**. Demotion candidates and their sidecar homes are in the prior
   entry. Closes when a demotion PR merges, or the operator says in writing that 10
   bytes is acceptable.
4. **Optional: round 5 delta audit of the merged #940** (repo: devrc;
   `scripts/browser-bridge/reference/agent.md`). Unchanged.
5. **Measure the toast row of the vetr discriminator** (repo: devrc; file
   `scripts/browser-bridge/reference/sites/vetr.com.md`). The only branch of the shipped
   procedure that rests on library behaviour rather than a measurement, and the file
   says so. Recipe is in the "Open investigations" block above. Closes when the results
   table carries a toast-present row.
6. **Root-cause the tired-tab stall** (repo: devrc, `scripts/browser-bridge/`). See the
   investigation block for the six eliminations already paid for and the one probe that
   separates the remaining fork. Closes when the mechanism is named in
   `reference/spa-wake.md` (it is bridge-generic, not vetr-specific), or when an
   operator dismisses it in writing as not worth chasing.
7. **Consider adding `vetr.com` to `CLIENT_DOMAINS`** (repo: devrc;
   `scripts/testlib/client_host_scan.py:78-81`). devrc is PUBLIC and `vetr.com` is not
   in the redaction list, so nothing flagged that `vetr.com.md` names a real business's
   live payment rail and admin login URL. **Operator ruled 2026-08-28: "disclosure is
   not severe, ignore"** — so this is NOT a defect to fix, and the file stays as
   merged. Listed only so the next session does not re-raise it. Closes as already
   decided.

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

## How to verify

```bash
# 1. the entry is live in the INSTALLED clone (what the running bridge reads)
cd ~/workspace/devrc/scripts/browser-bridge && nix develop --command python3 -c "
import sys; sys.path.insert(0,'.')
import server as S
for h in ['civitai.com','vetr.com','app.vetr.com','admin.vetr.com','notvetr.com','127.0.0.1']:
    print(f'{h:16} -> {S._site_notes_path(h)!r}')"
# expect: civitai.com (POSITIVE CONTROL) and the three vetr hosts hit; the last two ''.

# 2. the registry ledger is intact (negative control: drop the entry -> this goes RED)
cd ~/workspace/devrc/scripts/browser-bridge && nix develop --command \
  python3 -m pytest tests/test_site_notes.py tests/test_skill_size.py -q     # expect 75 passed

# 3. the squash landed BY CONTENT (ancestry is false forever after a squash)
git -C ~/workspace/devrc cat-file -e origin/main:scripts/browser-bridge/reference/sites/vetr.com.md && echo present
```
