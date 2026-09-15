# `handoff-tmux-webapp` — CLOSED ranked items, evicted 2026-09-14

Verbatim copy of the CLOSED items evicted from `claudedocs/handoff-tmux-webapp.md`'s
`## Next steps (ranked)`, across two sweeps on 2026-09-14. **Nothing was deleted** — the live doc
stood at 327,624 B of a 327,680 B budget (56 B of headroom), so the next routine handoff write
would have turned `test_no_handoff_doc_exceeds_its_budget` red on `main` for everyone.
`claudedocs/refs/` is exempt from that test; `claudedocs/handoff-*.md` is not.

These items are real lessons, not just status, which is why they were demoted rather than dropped.
The pre-eviction doc is also at `git -C <devrc> show 08e052f0:claudedocs/handoff-tmux-webapp.md`.
⚠ `08e052f0` is the last commit that TOUCHED the doc before the sweep, **not** the sweep's parent
— `6c8c94d2^` is `70c4a006`, with three commits in between, none of which modify it. The two blobs
are identical, which is what makes the citation valid; the adjacency is not.

⚠ **The item COUNT and the `🔴` COUNT that used to stand here are gone on purpose.** Both were
prose totals kept beside the thing they counted, nothing asserted on either, and both had already
drifted: the header said 47 items when the roster held 49, and "68 `🔴` markers" was wrong at the
base of the very PR that quoted it (the true figure was 73). **Count the `Evicted:` roster below if
you need a number** — it is the one list a reader can check against the entries.

🔴 **Numbers here are RETIRED, never reused.** A rank is half a `claim-work` claim's identity, so
re-minting one of these would point a new claim at closed work.

Evicted: 1–47, 50, 51, 52, 55, 58, 61, 62

The live queue therefore holds exactly: 48, 49, 53, 54, 56, 57, 59, 60.

🔴 **THE SECOND SWEEP EVICTED SIX — 9, 17, 25, 26, 42, 45 — AND EVERY ONE HAD BEEN CLOSED FOR DAYS
WHILE STILL READING AS OPEN.** Six of the fifteen entries the queue then advertised were finished
or fictional. **The mechanism is MEASURED, not guessed: the first sweep keyed on each rank's
HEADING LINE, and every closure recorded only in a rank's BODY survived it.** The predicate is
**a heading carrying `✅`, `DONE` or `CLOSED`** — state it, because on bare `✅` the same data
gives 59/62 and a reader who guesses that spelling concludes the claim is wrong. It predicts
eviction for **58 of 62** ranks. The four exceptions:

- **53 — false positive.** Its heading says `WORKBENCH IS NOW DONE` while its laptop half is
  genuinely open. A defect of the predicate, not a counter-example to the mechanism.
- **61 — the marker was in a THIRD place: a `###` heading in ANOTHER SECTION.** `6c8c94d2^` line
  2456 carries `### ✅ RESOLVED 2026-09-14 — rank 61 clawgate-e2e TaskRunTimeout is NODE I/O…`,
  under "Open investigations" rather than in the rank body. So 61's eviction is EXPLAINED, and it
  is the worked example of a location neither the heading scan nor a body scan reaches.
- **46, 62 — no marker anywhere, evicted anyway. 🔴 WHY IS GENUINELY NOT EXPLAINED.** Both closed
  before the sweep commit (`6c8c94d2`, 2026-09-14T05:57:57Z) — `#797` at 09-11T23:39:55Z (2.3
  days), `#1660` at 09-14T03:01:03Z (~3 h) — and neither closure is written anywhere in the
  pre-sweep doc. **Do not supply a mechanism**: an earlier draft guessed "closed by the sweep
  session itself" and that was false for two of the three it named. ⚠ An audit round reports
  attributing these to session ids from transcripts; that was not re-verified here, so it is not
  asserted here.

🔴 **THE MARKER LIVES IN THREE PLACES, NOT TWO — AND A SCAN THAT READS ONLY THE RANK BODY MISSES
THE THIRD.** That is the correction that matters to anyone building the deterministic version:

| where the closure was written | ranks | first sweep |
|---|---|---|
| the rank's HEADING line | 58 of 62 | evicted ✅ |
| the rank's BODY only | 17, 25, 26 | **survived** ❌ |
| a `###` heading in ANOTHER SECTION | 61 | evicted ✅ |
| nowhere at all | 42, 45, 46, 62 | 46 and 62 evicted; **42 and 45 survived** ❌ |

🔴 **SO THE NO-MARKER CLASS IS FOUR — 42, 45, 46, 62 — AND THE SWEEP CAUGHT TWO OF THEM BY MEANS
THIS DIAGNOSIS DOES NOT ACCOUNT FOR.** A body scan alone would have left two of the four. What IS
established is the FALSE-NEGATIVE direction: **no rank with a body-only marker was ever evicted** —
measured over all 62. Why 46 and 62 were caught is open.

🔴 **AND A THIRD WAY A RANK GOES STALE THAT NO MARKER SCAN CAN SEE: ITS INSTRUCTION ROTS WHILE ITS
STATUS STAYS TRUE.** Rank 47 was never closed, never marked, and read as perfectly live — while the
`cg.tmux.group.*` prefix it told you to measure was retired **1 h 44 m after it was written**, by
`#797`, a PR of this same arc. The item stayed open and correct-looking; only its *method* died.
Run literally it returns a structural zero and reads as a confident measurement of nothing.
**A rank that names a key, path, flag, selector or command is carrying a DEPENDENCY nobody
re-checks** — so when a sweep meets an item whose instruction names a concrete identifier, verify
the identifier still exists before running it, and treat "the result was zero/empty" as the
suspicious case rather than the clean one. No scan over CLOSURE markers will ever catch this class,
because nothing about the item is closed.

The remedies, by class:

- **Body-only (17, 25, 26)** — a scan that reads the WHOLE rank body catches all three, in both
  sweeps. This is the cheap half and it is the one worth automating.
- **Another section (61)** — the scan must also read `###` headings elsewhere in the doc, or it
  reproduces the miss one level up.
- **No marker anywhere (42, 45, 46, 62)** — only re-measuring against the code catches these.
  Nobody had noticed `#749` closed 42; 45 was orphaned the moment rank 51 was evicted out from
  under it.

🔴 **RETRACTED — "the first sweep evicted what was *marked* closed; these were not marked".** That
was this file's own first explanation of the miss and it is FALSE: 17, 25 and 26 were all marked,
in the body, at `6c8c94d2^` — the exact tree the first sweep ran against. Caught by a round-0 audit
and confirmed by measurement. It matters because the wrong diagnosis prescribes the expensive
remedy (re-measure everything) for a class the cheap one (read the whole body) already covers.
**Do not re-derive it.**

🔴 **The deterministic version does not exist yet, and it is the thing that would end this.** A
test failing when a live-queue rank body carries `✅ DONE`/`CLOSED` is mechanically checkable off
`handoff_index._next_step_units`, which already parses exactly that structure — and nothing
cross-checks this file against the live doc today (`git grep -n "closed-ranks" scripts/ claude/`
returns zero). It needs care rather than enthusiasm: **rank 53 is its first false positive**, and a
predicate that red-lines an open item trains everyone to click through.

---

1. ✅ **DONE 2026-08-27** — the idle reaper had never fired because it COULD NOT (`time.NewTicker`
   delivers its first tick one whole interval in). `ZacxDev/homelab-infra#457`, 0.8.7.
   forcing: none

2. ✅ **DONE 2026-08-27** — detached suggest POST, `ZacxDev/homelab-infra#451`. 8030ms → 22ms.
   forcing: none

3. ✅ **DONE 2026-08-28** — read-only `capture-pane` rendering (`devrc#992` +
   `ZacxDev/homelab-infra#496`), 0.8.10.
   forcing: none

4. ✅ **DONE 2026-08-28** — tmux snapshot ingest + host-side pusher (`ZacxDev/homelab-infra#468` +
   `devrc#974`), 0.8.8. Proof was an UNATTENDED tick.
   forcing: none

5. ✅ **DONE 2026-08-29 — `ZacxDev/homelab-infra#516`, squash `c8635976`.** `requireTerminalToken`:
   the ONLY fail-closed tier. 🔴 **The secret is still UNPROVISIONED and the surface boots DISABLED
   — correct, not a regression.** The SOPS age identity is on NEITHER host. Wired `optional: true`.
   **To arm it:** `clawgate gentoken` → `sops clusters/workbench/apps/clawgate/secrets.enc.yaml`.
   forcing: none

6. ✅ **FULLY DONE 2026-08-31 — `ZacxDev/homelab-infra#527` + `devrc#1056` (squash `ac64ccb4`).**
   Sentinel observed non-null end to end on both hosts. 🔴 **A PANEL STORES A DESCRIPTION, NOT A
   REFERENCE** — no field is both unique and stable across 79 live windows, so panels resolve
   against the live snapshot on every read. The laptop's `start_time` is Jan 2021: an OPAQUE
   EQUALITY TOKEN, never a timestamp.
   forcing: none

7. ✅ **DONE 2026-08-30 — `ZacxDev/homelab-infra#538`, squash `fb9b75e5`.** The htmx layout tab.
   🔴 **THE UI TIER CARRIES ONLY REVERSIBLE CONTROLS, BY CONSTRUCTION** — the destructive control
   was REMOVED, leaving `clawgatectl panel rm` as the only delete path.
   forcing: none

8. ✅ **DONE — all five sub-items closed.** 8a RECURRING (measured green 2026-08-31), 8b/8e done,
   8c `ZacxDev/homelab-infra#591` squash `d6dc52cf`, 8d `#592` squash `d2d2346e`. Both verified on
   the merged tree in BOTH tiers (go 20 ok/0 FAIL; bats 67 ok/0 not ok; `ALL LEGS PASS` on
   `clawgate-ci-rerun-z5wj5`). Audited post-merge — findings became ranks 14–16, not a revert.
   forcing: gate — `clawgate-e2e` was green through all four of #538's audit rounds while running
   ZERO specs touching layout. 8b and 8e closed both halves of that.

10. ✅ **DONE 2026-09-02 — `ZacxDev/homelab-infra#637`, squash `97aed04d`.** All four background
    loops now hold a cadence guard; the gap was measured, not assumed: `NewTicker`→`NewTimer` on
    `RunSweeper` **SURVIVED the whole `internal/api` package at `origin/trunk`** (reproduced twice,
    from independent `git archive` extracts) and is **KILLED at HEAD**. Content-verified on trunk:
    `114 file(s) parsed, 4 loop(s) audited`, all four subtests green.
    🔴 **The seam row is the load-bearing one:** with `RunReconciler` mutated, `internal/agents`'
    OWN package suite stays **green** while the cross-package ledger reds. A per-package guard is
    structurally blind to that loop's own defect, which is why the walk is module-wide.
    **Design:** both sets DERIVED, never spelled — push-deciders by signature, spawner-style nets by
    shape — with a floor keyed by directory (`internal/agents:(*Provisioner).RunReconciler`) so a
    walk that never leaves `internal/api` fails rather than guarding less than it claims.
    🔴 **EIGHT AUDIT ROUNDS, ending on a clean one.** Findings 4 → 2 → 1 → 1 → 0; payload
    1056 → 293 → 121 → 46 → 28. **Four consecutive rounds hit ONE shape: the fix for a false
    positive silenced the arm that caught the real thing.** Sharpest instance — a comment asserting
    *"at function scope it does not compile"* about `:=` was **false about the Go spec**, and
    silenced a real mutation for a whole round: `interval, tuned := interval*10, true` redeclares
    and WRITES the parameter, builds rc 0, vets rc 0, ledger `ok`.
    Every arm now carries its own violation code; 9 of 11 messages name a **measured** correct shape
    they reject plus a repair, 2 are bare because nothing has been measured for them.
    ⚠ **Known and disclosed, not defects:** two correct shapes on the stop arm are left red by
    decision (every excuse mechanism tried reopened the live-path hole three rounds running, and
    neither shape occurs in this module); and **every arm in this file rejects at least one
    plausible correct shape** — a property of a guard this strict, mitigated by the routing, not
    removed by it.
    forcing: none

11. ✅ **DONE 2026-09-02 — `ZacxDev/homelab-infra#640`, squash `27a09792`.** Content-verified on
    `origin/trunk` (never ancestry): drawer key attribute, `__cgLayoutDrawerInit`, the shell call
    site, the e2e spec and the Go guard all present; nonexistent-marker grep 0 as the control.
    🔴 **THE DOC'S OWN TITLE FOR THIS ITEM WAS WRONG, AND THE IN-CODE COMMENT HAD ALREADY SAID SO.**
    It was never "write-triggered". Since `#611` the panel subscribes to `sse:tmux.changed`, so an
    outside agent posting a tmux snapshot **on its own 2-minute timer** snaps the drawer shut
    mid-read, with no action of the operator's. A maintainer told only "it closes when you write"
    cannot reproduce the report they are handed. **Read the code's comment over this list's summary
    of it** — the code had been corrected and the rank line had not.
    **What landed:** `layoutDrawerScript`, the prescribed `taskCardScript` treatment. Three
    constraints, none of them stylistic:
    🔴 **The key is the VIEW ID, not the archived count.** `data-layout-archived` holds a number
    that CHANGES on archive/restore — the very swaps being survived — so a count-keyed memory
    forgets the drawer exactly when it matters. New `data-layout-archived-drawer` carries the view
    id. #527's lesson one layer up: store a REFERENCE, not a DESCRIPTION.
    🔴 **It cannot live in the panel**, for two independent reasons: the panel body IS the swap
    target (`hx-swap: innerHTML`), so a script inside it is destroyed by the swap it exists to
    survive; and `layout.spec.ts` forbids any `on*`/`hx-on*` attribute inside `#panel-layout`, so an
    inline handler is not available either. Hence the page shell.
    🔴 **It must not swallow the summary click** — mutant PD, named in `layout.spec.ts`. The
    listener only OBSERVES toggles and writes `open` solely in `restore()`.
    Binding is once-guarded with state on `window`, and the listeners are on `document`, NOT
    `document.body`: the guard plus a body-scoped listener is worse than either alone, because an
    hx-boost body swap would kill the listener while the guard suppressed the rebind.
    **Verification matrix:** the Go keying guard was watched RED at `origin/trunk`
    (`found 0 drawers carrying data-layout-archived-drawer, want 2`, from a `git archive` extract)
    and GREEN at HEAD; the fixture pairs view 7 with 3 archived and view 8 with 2, so no id equals
    its own count and a count-keyed implementation is visible rather than passing by coincidence.
    The e2e case passed at HEAD (4.7s), and mutation **M1 — the narrowest that can be wrong**
    (script still defined, call site removed) was **KILLED at the behavioural line**, the locator
    resolving to `<details data-layout-archived="1" data-layout-archived-drawer="1">` with `open`
    **false**; everything before it passed, so the swap demonstrably happened and only persistence
    was lost. Full `layout.spec.ts` 5/5 including the ladder's summary-click-FLIP assertion.
    Module: build 0, vet 0, `go test ./...` 20 ok / 0 FAIL from the runner's own lines.
    ⚠ **`clawgate-e2e` reported 128 tests, up from 127** — that is the new spec running in CI, and
    it is the only proof the spec is not local-only.
    🔴 **MERGED WITH ALL FOUR CHECKS PENDING ON THE MERGED SHA, BY OPERATOR DECISION — so "the
    checks are green for this change" is a claim NOBODY CAN MAKE, and must not be inferred later
    from the merge.** The branch was rebased onto trunk (`ea98254a` → `3e08acaf`) to gate the
    MERGED tree, which re-queued every check; they had not reported by merge time and still had
    not afterwards. The pre-rebase red was `clawgate-ci` and is diagnosed under rank 18.
    ⚠ **`/audit-pr` was NOT run on `#640`.** Offered and declined; recorded, not hidden.
    forcing: none

12. ✅ **CLOSED AS REFUTED 2026-09-01 17:02Z — there was never a defect. NOT MINE, and NOT A FIX.**
    Clawgate task #463 reads `status: complete`, and 🔴 **`complete` here does NOT mean the bug was
    fixed — the board has four statuses and none of them is *invalid*/*wontfix*.** Its closing
    comment says so verbatim: *"there is no fix, and there must not be one."*
    **What the original 60-of-248 measurement actually counted:** 59 of the cards sit inside the
    deliberately collapsed `Done` `<details>`, and 3 are final-screenful cards fully visible at the
    bottom of the viewport. 59 + 3 = 62, the exact count, no residue. Chromium implements
    `::details-content` with **`content-visibility: hidden`** (measured via
    `getComputedStyle(d, '::details-content')`, not assumed), so descendants of a CLOSED `<details>`
    still return non-zero `getBoundingClientRect()` — any script enumerating
    `article[id^="task-"]` and comparing rects counts collapsed cards as laid out. The count was
    honest; the SET was wrong. `document.scrollHeight` was correct all along.
    `trulyOffscreenAtMaxScroll` is **0** at both 1280x720 and 390x844.
    🔴 **Criterion 2 of that task is a TRAP — do not attempt it.** *"The count of cards with
    document-y beyond maxScroll is 0"* is unsatisfiable by any correct scrollable page: the final
    screenful always has `top > maxScroll` while being fully visible. An agent picking it up would
    "satisfy" it by expanding the container, paginating, or deleting content — damage in service of
    a bar a healthy system cannot clear. **Ask what a healthy system scores before writing a bar.**
    The one REAL finding was split out as clawgate #468, fixed and deployed in 0.8.21 (and #468's
    own recorded premise was wrong too — `scrollIntoView` does not auto-expand a closed `<details>`,
    so it repaired a live failure rather than pinning a browser detail).
    forcing: none — closed. Left in place, not deleted, because the refutation is the artifact: the
    measurement that filed it is reproducible and still reads as a defect to anyone who repeats it.

13. ✅ **DONE 2026-09-01.** `--slug-for` discarded a lettered sub-rank instead of rejecting it, so
    `8c`, `8d` and every lettered sub-rank of every rank minted ONE slug — and the collision was
    reported as **rc 12 “ALREADY YOURS, carry on”**, the one answer that means PROCEED. Measured
    before the fix on this doc: `8c` and `8d` both printed `tmux-webapp`.
    🔴 **The pattern was widened AND the class closed:** an unparseable rank (`8-c`, `8.1`,
    `part2`) is now a usage error, not a silent drop, because the hazard was an ignored rank rather
    than the spelling `8c`. The rank is also case-folded (`8C` == `8c`; `validate_slug` is
    lowercase-only, so unfolded it was rc 2 at claim time). 7 of 8 new cases watched RED on the
    pre-change `origin/main`; the 8th is labelled in place as an invariant guard, not counted as
    regression coverage.
    forcing: none

14. ✅ **DONE 2026-09-01 — `ZacxDev/homelab-infra#632`, squash `b2fecf49`.** Content-verified on
    `origin/trunk` (never ancestry): the file went **168 → 558 lines**, all three new functions
    present, with a nonexistent-marker grep returning 0 as the control that the check discriminates.
    🔴 **It was merged over a RED `tekton/gitops-validate`, and that is NOT a claim the leg passed.**
    The check text said `FAILED: scripts-tests` while `step-scripts-tests` **exited 0** — the
    documented text-vs-PipelineRun disagreement; the real failures were
    `test_s3_public_bucket_allowlist.py` and `test_loki_ruler_rules_wired.py`. **The discriminating
    control, measured, not theorised:** `gitops-validate-wb42q` (rev `d2b775d9`, not mine) fails the
    first and `gitops-validate-9gckk` (rev `2fe700b9`, not mine) fails the second, while mine
    (`78rgf`, rev `94cf920e`) fails both — and this diff is ONE Go test file under
    `containers/clawgate/`. The legs that DO cover it were green: `clawgate-ci` pass (build/vet/test
    **-race** + extension + hook bats), `clawgate-e2e` pass (127 tests, 2 skipped),
    `ux-audit-clawgate` pass. **Do not later infer from the merge that `gitops-validate` was green
    for this change** — nobody can make that claim.
    **What landed:** `push_fanout_ledger_test.go` now pins the RELATIONSHIP, not a call-site count.
    Both sets are DERIVED, never spelled: push-deciders = transitive callers of `goPushBroadcast`
    (39 on trunk today), spawners = any function with a func-typed parameter and a `go` in its body
    (so a sibling of `safeGo` is covered the day it is written).
    **Measured in the worktree, not carried over from the audit that filed this:** bug = flip
    `notifyAgentRunning`'s running-status skip, so a task-linked NON-running agent pushes.
    D1 (bug alone) → `TestProvisioningPushSkipsNonRunning` **20/20 caught**, new guard silent
    (correct — not a scheduling change). D2 (bug + the call wrapped in `safeGo`) → behavioural
    **1/20 caught**, new guard **20/20 red**. 🔴 **The doc previously recorded 0/20; the measured
    value here is 1/20** — same direction, and the residual 1 is timing luck, because D2 converts a
    deterministic detector into a race. The guard's own line names the closing condition verbatim:
    `server.go:2160:52: BroadcastAgentChanged reaches notifyAgentRunning via safeGo`.
    🔴 **The decisive control: `TestEveryPushFanOutGoesThroughTheOneChokePoint` stays GREEN under
    BOTH D1 and D2.** Wrapping a CALLER moves no `push.Broadcast` call site, so the old ledger
    cannot see this — that is what made it a real gap rather than a duplicate guard.
    Mutation battery **6/6 killed**, M0 control green, restore verified by hash (M1 safeGo-wrap /
    M2 bare `go` → guard red, old ledger green; M3 seed renamed → coverage floor; M4 spawner
    detection blinded → `safeGo` floor; M5/M6 finder narrowed → finder control). A 7th mutant
    (dropping the status clause outright) was scored **INVALID, not a kill** — it left the `agents`
    import unused and did not compile.
    Anti-vacuity in the file: a 9-name coverage floor, a `safeGo` spawner floor, and a
    positive/negative control with one case per boundary claimed plus three it must stay silent on
    — including `goPushBroadcast`'s own goroutine, flagging which would make the guard permanently
    red. Known limits are stated in the file: it follows CALLS not function VALUES (so
    `notifyTaskCreated`, which hands `flushCreatedTasks` to a timer, is correctly absent — that path
    is async by design and the barrier never covered it), it is lexical and single-package, and
    names are not receiver-qualified.
    Full module at that tree: `go build` rc 0, `go vet` rc 0, `go test ./...` rc 0, **20 ok / 0
    FAIL** counted from the runner's own lines.
    forcing: regression — measured, the conversion took one bug class from a 20/20 detector to a
    1/20 one, and until #632 merges the guard that would catch the enabling refactor is unmerged.

15. ✅ **DONE 2026-09-01 — `ZacxDev/homelab-infra#625`, squash `0dd62cd9`.** All three parts.
    (a) KNOWN LIMIT 4 said no test body uses a heredoc; two do, and `/^}/ { inbody = 0 }` is shared
    by BOTH scanners, so a column-0 `}` in one blinds them for the rest of the body while EXAMINED
    stays positive and the BODIES cross-check still agrees. Measured on a probe of that shape:
    pre-change `EXAMINED 3` and **ZERO violations**; post-change the real violation is caught and
    the heredoc's own `! grep` correctly ignored as data. Heredoc tracking added at all FOUR sites
    (2 scanners x 2 suites — no shared library) from one string, asserted byte-identical.
    🔴 **Two traps it cost, both now in the code:** the scanner may not SPELL its own redirect
    operator anywhere in its program (written literally in its own regex it opened a heredoc tagged
    `A` on that line and ran blind to EOF — 33 bodies against 35 by grep; the pattern is assembled
    in `BEGIN` now), and prose describing the operator needs an explicit comment clause — which
    **SURVIVED** a green run until a fixture body pinned it, exactly the rule-no-mutant-can-kill this
    file's own header warns against.
    (b) KNOWN LIMIT 5 ENFORCED. Measured on bats 1.11.1: a function-style test with the trailing
    marker IS collected and run (2), without it is not (1). Neither scanner sees one and the BODIES
    cross-check is structurally blind (both sides count `^@test `), so they agree at the same wrong
    number. Detector carries its own positive control.
    (c) Both surviving mutants killed — one body each for egrep/fgrep/zgrep, and a `grepzilla` body
    for the TRAILING word-boundary class (only the leading one was pinned, by `pgrep`).
    Probe contract re-derived from a real run: **9 violations / BODIES 14 / EXAMINED 20**. Mutation
    battery **6/6 killed**, M0 control green, file restored by hash.
    forcing: none

16. ✅ **DONE 2026-09-01 — `ZacxDev/homelab-infra#618`, squash `b5992890`.** The `hook` leg read
    only `bats`' exit code, and `bats` on a file with zero `@test` bodies prints `1..0` and exits
    **0**. Each suite now runs separately against its own floor (34 / 31, from `bats --count`
    measuring 35 / 32 in the leg's own image). Measured with the mutation verified applied:
    pre-change `hook leg rc=0` verdict `pass`; post-change `rc=1` verdict `fail` — with `bats`
    itself exiting 0 in BOTH, so the floor is the only thing that spoke. Floors pinned by
    `scripts/tests/test_clawgate_ci_hook_floor.py` (extracts the script FROM the manifest;
    6/6 mutants killed by name). The leg's stale “11 tests” / “18 + 11” comments are gone.
    ⚠ **Deployed and verified against the DEPLOYED artifact, but no PipelineRun has run it yet** —
    `clawgate-ci` is path-filtered on `containers/clawgate/**` and this change touched neither, so
    nothing fired. The next PR touching that path is the end-to-end check: `step-hook` must show
    TWO plan lines and `floor=34` / `floor=31`. A single `1..67` means the Task did not reconcile.
    forcing: none

19. ✅ **DONE 2026-09-03 — `ZacxDev/homelab-infra#660`, squash `3d576f19`.** The tmux tab groups by
    PROJECT, not host: `<details>` per group, collapse remembered per project in localStorage, host
    as a per-card badge, groups ordered by their most urgent member (reusing `triageRank`
    unforked), and a group holding a WAITING window renders open **display-only** — the operator's
    stored collapse is never overwritten and returns when the window stops waiting.
    🔴 **`label` IS A TRAP AND THE NEXT READER WILL REACH FOR IT: 74 of 93 live windows carry
    `label_source: codename`** (Gold, orange, wheat…), so grouping on `label` yields ~20 codename
    pseudo-groups. `path` is 93/93 populated and yields 15 real groups. `tmuxSessionName` is not the
    project either — one session held windows from several repos.
    All derivation sits behind ONE `projectOf()` seam: `repo` field if present → leaf of `path` →
    `Other`. The `repo` branch is tested but DEAD until rank 21 lands.
    Four audit rounds; claims blocks on the PR.
    forcing: none

20. ✅ **DONE 2026-09-04 — `ZacxDev/homelab-infra#667`, squash `b49db6d1`.** Auto-approve is a header
    button + body-level dropdown (`#auto-approve-popover`, reusing the existing `hidden fixed
    right-3 top-16` pattern); the shell takes ONE width from `contentWidth()`, used by both the tab
    container and the header row so they cannot drift, capped at `2xl:max-w-[96rem]` (1920 and 3440
    both render 1536px centred, deliberately — an unbounded column gives ~3400px lines).
    🔴 **THE LOUD "auto-approve ALL is ON" BAR STAYS IN PAGE FLOW, BY DESIGN.** Only the per-project
    list moved into the dropdown. Folding the global bar in would hide, behind a click, the fact
    that every request is being auto-approved. The header button carries an amber count so "on" is
    legible without opening it. Do not "tidy" this later.
    It also fixed a PRE-EXISTING funnel-walk defect (see rank 23's history).
    forcing: none

21. ✅ **DONE 2026-09-04 — `devrc#1282`, squash `34ee2a376`. Merged, SHIPPED to both hosts, and the
    closing condition verified on the LIVE page.** All three legs, measured:
    **(a) on the wire** — `scripts/session-manager --json` against the real fleet: 92 rows, **90
    carrying `repo`**, both hosts `repos_measured=true` / `repos_status=ok` / `unparseable=0`; the 2
    without are honest `not_a_repo`.
    **(b) `projectOf()`'s first branch is no longer dead** — the consumer half was ALREADY MERGED
    when this started (`internal/api/tmux_ui.go:83` decoder, `:167` passthrough,
    `internal/ui/tmux.go:322` precedence), so this was a **devrc-only** change, not the two-repo
    lockstep the old entry described.
    **(c) two worktrees of one repo group together in the UI** — `/ui/tmux` on live `0.8.24`:
    `datapacket-talos` 39 windows, `devrc` 13, `homelab-talos` 11, **each spanning laptop AND
    workbench**; group count 15 → 14; and `clawgate-extension` no longer appears anywhere on the
    page — the laptop worktree that formed its own pseudo-group is filed under `homelab-talos`, on a
    path that exists on only one of the two machines.
    🔴 **`rev-parse --show-toplevel` IS THE WRONG CALL AND IS THE ONE A READER REACHES FOR** — on a
    linked worktree it returns the WORKTREE, i.e. exactly the defect this rank existed to remove.
    `--path-format=absolute --git-common-dir`, parent = the main clone, is the mechanism.
    ⚠ **The old entry's "most of the long tail" was REFUTED on measurement** — the live group count
    moved 15 → 14, one collapse, because almost every pane sits at a repo root where the leaf already
    equals the repo name. The fix is correct; the magnitude was overstated.
    Two audit rounds (5 payload 🟡 → 7 fixed → 2 🟢 → ended). Full history in the audit-claims blocks
    on the PR.
    forcing: none

22. ✅ **DONE 2026-09-04 — `/audit-pr 667` ran, and its findings became `ZacxDev/homelab-infra#680`,
    squash `0c5b35bf1`.** Round 1 on `#667` found no 🔴 and four 🟡; the fixes then took a
    **four-round delta ladder** (5 payload 🟡 → 2 🟡 + 5 🟢 → 2 🟡 + 2 🟢 → 1 🟡 + 2 🟢 → closed).
    Every round's claims block is on `#680`.
    🔴 **The two findings worth remembering:** (i) the primary server-side fix was **largely inert in
    production** — two lazy pruners still evicted silently, so on a 60 s sweep the broadcast fired
    when the app was idle and was starved exactly when it was busy; and (ii) a later round put
    **unbounded store I/O on the permission-decision path** (`autoApproveActive`, called from
    `POST /api/request`), where a Postgres restart would stall an agent indefinitely.
    🔴 **THE SAFETY PROPERTY IS MEASURED, NOT ARGUED:** a 48-row differential decision table (global ×
    project × query, healthy vs wedged store) is **sha256-identical** across base and head, WITH a
    positive control — a mutant that skips the in-memory delete moves 12 of 48 rows, 4 with a flipped
    decision. A 48/48 without that control would not have been evidence.
    ⚠ **Deliberately left unfixed, so they read as open rather than absent:** `aaOffScreen` only
    understands `px` offsets; `aaEvictsIn`'s `maps` arm matches the literal package name so an
    aliased import evades it; and `autoApprovePersistNotice` embeds `err.Error()` verbatim, which for
    a pgx dial failure can carry host/port/user/database into the operator's browser (behind session
    auth, and the actionable detail is the point).
    forcing: none

23. ✅ **DONE 2026-09-04 — and TWO OF ITS THREE PARTS WERE ALREADY CLOSED WHEN CHECKED.** The item
    was carrying them as open work; only (a) was real, and it took ten minutes.
    **(a) MERGED — `ZacxDev/homelab-infra#690`, squash `48d67f9af`.** `task_detail.go` and
    `task_not_found.go` now take `contentWidth()`; verified on `trunk` by content, including the
    NEGATIVE half — `operator.go` and `agents_detail.go` are untouched (`contentWidth` 0, `max-w-xl`
    2 and 3).
    🔴 **THE OLD ENTRY'S SAFETY RULE WAS RIGHT AND THE CHECK DERIVED FROM IT WAS WRONG.** "Their
    `max-w-xl` pairs with `lg:pl-72`" is the correct SAME-ELEMENT criterion — but a file-level
    `grep -c lg:pl-72` cannot see which element carries it, and BOTH changed files contain the
    string. Measured: `task_detail.go:76` puts `Class("lg:pl-72")` on a WRAPPER with `Main(` at `:86`
    below it (safe, and identical to the shell's own shape at `components.go:141` since #667), while
    `operator.go:72` puts the offset and `max-w-xl` on the SAME `Main(` element (unsafe). The guard
    that shipped pins the relationship as an **iff** over all five documents — a column is sized by
    `contentWidth()` ⟺ it does not itself carry the offset — comparing width TOKEN SETS rather than
    grepping a class name, so a new off-shell route is classified by its own markup rather than an
    allowlist. One mutant renames the offset utility and the guard fails LOUD (*"every branch in this
    file is now vacuous"*) instead of silently reclassifying. It also corrected a false comment in
    `task_detail.go` that claimed the offset sat on its `<header>` — the very misreading that
    produced the wrong check.
    **(b) WAS ALREADY FIXED; the "still skips" line was stale.** `clawgate-funnel.audit.ts:186`
    carries a full `view("auto-approve-armed", …)` block that seeds a pending checkpoint, asserts it
    visible BEFORE arming (so the later assertion is about the arming, not the seeding), arms via the
    real control, and asserts the card survives beneath the banner — its own comment reads *"🔴 THE
    ASSERTION THIS CHANGE EXISTS FOR. On pre-change code there is no card here at all."*
    **(c) DISMISSED under its own criterion 2** — clawgate task #495 is `complete` with the written
    dismissal recorded (comment 853), naming what was checked: `ReceivedAt` is stamped and read in
    the SAME process so a negative age needs multi-replica skew or an NTP step-back (sub-second);
    the task's own "nobody has observed a negative age in production" is still true; the failure is
    bounded and self-correcting via the 2-minute push, so it cannot latch. Dismissed rather than
    fixed because the remedy has **no observable trigger** — no counter or log would ever say it
    mattered — so it would trade a simple mutation-covered path for an uncovered case that has never
    fired. Criterion 1 stays written and ready if one is ever observed.
    forcing: none

24. ✅ **RETIRED 2026-09-04 — the conversation happened and the items are filed as ranks 25–30.**
    It was a placeholder for a conversation, and its closing condition ("the operator names the
    annoyances, each becomes its own ranked item") is met. The operator named five, and 🔴 **the
    recon that followed refuted two of this doc's own standing claims** — see rank 25's entry for
    the stale `AskUserQuestion` line and rank 29's for the ledger that already exists. Original
    brief preserved below because the METHOD is the artifact: ask, then measure, then build.
    **Another UI/UX feedback pass on the clawgate web UI.** Repo: `ZacxDev/homelab-infra`,
    `containers/clawgate/internal/ui/` (Go-built HTML + htmx; there are no template files), with
    e2e in `containers/clawgate/e2e/tests/` and the visual walk in `e2e/ux-audit/`.
    **Stated by the operator at the end of the 2026-09-04 session: "we'll do another UI/UX feedback
    pass next session."** That is the whole brief so far — the specific items do not exist yet.
    🔴 **START BY ASKING, NOT BY BUILDING.** This effort's UI items have all come from the operator
    naming a concrete annoyance: rank 19 (group by project, not host), rank 20 (auto-approve into a
    header control; one width from phone to ultrawide), rank 23a (two routes left at the old width).
    None was discoverable from the code. An audit will not produce them either — it scopes to a
    diff. **The first move is a conversation, and the second is a measurement.**
    🔴 **MEASURE THE SURFACE BEFORE CHANGING IT — this doc has been burned twice.** Rank 21's
    "a worktree pseudo-group is most of the long tail" was REFUTED on measurement (the live group
    count moved 15 → 14, one collapse). And `#667`'s width table was wrong in **three** bands because
    it quoted the `max-w-*` cap and ignored the `lg:pl-72` sidebar. **Probe the live page and read
    the real numbers**; `/ui/tmux` is the tab PARTIAL (no header at all) and `/` is the shell — ask
    which route renders the thing you are asking about.
    **What is live to look at right now:** clawgate `0.8.25`-era on the workbench
    (`clawgatectl health` is the only authority), carrying `#660`'s project grouping, `#667`'s header
    auto-approve control, `#680`'s persist-failure notice and server-clock expiry, and `#690`'s
    `contentWidth()` on the task routes. `http://192.168.50.250:30302` on the LAN (no human auth),
    `https://clawgate.zacx.dev` behind Authelia.
    ⚠ **Known-and-accepted, so they do not get re-reported as new findings:** the loud
    "auto-approve ALL is ON" bar stays in page flow **by design** (folding it into the dropdown
    would hide, behind a click, that every request is being auto-approved); a **pre-existing** shell
    overflow of 4 px at 360 and 44 px at 320 that predates `#667` and is deliberately outside the
    responsive spec's swept widths; and `agents_detail.go`/`operator.go` must NOT be widened —
    their `max-w-xl` sits on the SAME element as `lg:pl-72`, unlike the task routes where the offset
    is on a wrapper.
    **Closing condition:** the operator names the annoyances, each becomes its own ranked item with
    its own closing condition, and this item is retired once they are filed — it is a placeholder
    for a conversation, not a unit of work to be graded.
    forcing: user — the operator asked for it explicitly at the close of the 2026-09-04 session.

27. ✅ **DONE 2026-09-05 — `ZacxDev/homelab-infra#696`, squash `17cd8a839`**, content-verified on
    trunk, after a six-round audit ladder (see the status block at the top). Claim released.
    **Two attention-queue accuracy defects.** Repo: `ZacxDev/homelab-infra`. Originally —
    dispatched 2026-09-04, claim `tmux-webapp-27`.
    🔴 **(a) A `question` entry NEVER auto-resolves, so the queue overstates what is waiting on the
    operator — the one thing it exists to get right.** The reaper resolves only `idle`
    (`ResolveOpenIdleNotSeenSince`, `internal/attention/attention.go:~448`, sweeping every 30m for
    entries unseen 4h); **nothing** resolves a question, and no hook posts resolve for one.
    Measured: a question raised **2026-09-02T19:46:19Z** was still rendering as `blocked` and
    ranked first under "blocked first, then longest wait" **two days later**.
    ⚠ **The fix has an EMPTY-RESULT trap written into its brief:** "no further events from that
    session" is equally consistent with answered-and-quiet and with crashed-and-gone, so it
    identifies neither. The agent was told to name the discriminating upstream signal or state
    plainly that it could not and document the approximation's limits.
    **(b) The card body is clipped by `max-h-32 overflow-hidden` with no expand control** — long
    question bodies truncate with no affordance, which is exactly the "see the full context"
    complaint.
    🔴 **THE OPERATOR RAISED THE BAR MID-FLIGHT, AND IT IS A DIFFERENT KIND OF REQUIREMENT.**
    Verbatim: *"we need to detect that and retire the question so there is never a stale question
    visible to the user."* That is a **display-correctness guarantee**, not a background tidy-up —
    "it resolves eventually" does not satisfy it. Whoever closes this must state **which window of
    staleness can still be visible**, because there will be one.
    ⚠ **This answer arrived in response to a question about the WRITE path** (what should happen if
    you reply to a pane that moved on) and the operator redirected it to the READ path instead —
    retire the question rather than guard the write. That is the better fix and it relocates the
    work from rank 29 to here. The write-time race does not vanish, it only shrinks; see rank 29.
    **Two independent signals, and they cover each other's blind spot — measured, not assumed:**
    (1) **the session unblocked** — `AskUserQuestion` BLOCKS the session, so any later hook event
    from that session id proves it is no longer waiting; blind to a dead session. (2) **the pane
    moved on** — the 2-minute snapshot already carries pane content, so a pane no longer showing
    the pending prompt is not awaiting an answer; works when hooks never fire, but its cadence
    means the guarantee can never be absolute. Use both; name what each cannot see.
    Closing condition: (a) a question answered in the terminal stops appearing as blocked, by a
    named mechanism, with a regression test watched RED at `origin/trunk` — the reproducible red is
    the 2026-09-02T19:46:19Z entry still rendering `blocked` two days later; (b) the full body is
    reachable from the card.
    forcing: none

28. ✅ **DONE 2026-09-05 — shipped in `#696`, squash `17cd8a839`.** Options are structured end to
    end and render as discrete rows; they are **display-only**, because the write path (rank 29)
    does not exist. Claim released.
    **Structured options on an attention entry.** Repo: `ZacxDev/homelab-infra`. Originally —
    dispatched 2026-09-04, claim `tmux-webapp-28`.
    🔴 **"Select a suggested option" has NO structured data behind it today.** Measured:
    `attentionRaiseRequest` is `Kind, Priority, Title, Body, Host, Project, SessionID, Cwd,
    TmuxPane` — **no options array anywhere in the payload, the store, or the render.** What the
    card shows is the options flattened into `Body` as plain text inside a `whitespace-pre-wrap`
    div. So this is a hook + payload + store + render change, not a UI tweak.
    🔴 **RENDER ONLY — the options are deliberately NOT wired to an action in this rank**, because
    answering needs the write path that rank 29 has to build first. Backwards compatibility is a
    requirement, not a nicety: entries raised by an OLD hook carry no options and must still render.
    Closing condition: an `AskUserQuestion` raise carries its options as structured data end to
    end and they render as discrete options, with options-less entries still rendering correctly.
    forcing: none

29. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#711`, squash `2d326d987`**, plus the host half
    `innovation-upstream/devrc#1324`, squash `f4bdb83a7`. Four audit rounds; the ladder ENDED on a
    round with no payload defect. Deployed as **0.8.26** and ARMED — see Status. Claim released.
    **Originally: the reply channel — host-side short-poll agent + the `send-keys` route + token
    provisioning.** Repo: `ZacxDev/homelab-infra` + `innovation-upstream/devrc`. **NOT STARTED,
    NOT DISPATCHED — deliberately held for a design conversation with the operator.**
    🔴 **THREE LAYERS ARE MISSING, NOT ONE. This is why "add a reply box" is not a UI item.**
    | layer | measured state, 2026-09-04 |
    |---|---|
    | write route | `requireTerminalToken` guards **ZERO** routes — the only non-test, non-comment occurrence in the repo is its own definition at `internal/api/auth.go:140`. `POST /api/tmux/send-keys` → **404**. |
    | token | Pod boot log, verbatim: `terminal write surface: DISABLED (fail-closed) — CLAWGATE_TERMINAL_TOKEN is not set`. Secret `clawgate-secrets` holds `CLAWGATE_AUTH_TOKEN`, `CLAWGATE_HOOK_TOKEN` (32 B), `CLAWGATE_SESSION_SECRET` — **no terminal key**. The deployment references it `optional: true`, so the pod boots healthy with it absent. |
    | return channel | Host→pod is **push-only** (`tmux-snapshot-push.timer`, 2 min, running `devrc/scripts/tmux-snapshot-push.sh` → `POST /api/tmux/snapshot`). **Nothing on the host pulls commands down.** The "tmux-agent with outbound long-poll" in this doc's architecture section was designed but only its PUSH half was ever built (rank 4). |
    **That is why today's "jump in" is a copy-to-clipboard of `tmux switch-client -t %N`** — it
    does not jump, it hands you a command to paste. Not a stopgap anyone chose to leave; it is the
    only thing possible without a return channel.
    🔴 **THE SOPS BLOCKER IS REAL BUT SOLVABLE, AND THIS DOC HAD IT HALF-RIGHT.** Rank 5 says "the
    SOPS age identity is on NEITHER host" — **re-measured true**: `~/.config/sops/age/keys.txt`
    absent and `SOPS_AGE_KEY_FILE` unset on **both** workbench and laptop. **But the private key is
    recoverable from the cluster**: `flux-system/sops-age`, key `age.agekey` — and its public half
    was compared against the recipient in `.sops.yaml` and **matches exactly**, so it is the right
    identity, not a stale one. Provisioning is therefore an operator step, not an impossibility.
    🔴 **Neither half is reproduced here, and neither belongs in a doc, a log, a PR or a fixture —
    this repo is PUBLIC.** Read it from the cluster at the moment you need it.
    🔴 **OPERATOR DECISION, TAKEN WITH THE BLAST RADIUS STATED — free text into ANY pane,
    unrestricted.** Offered and DECLINED: scoping free text to panes with an open attention entry.
    So the route, once armed, is a general remote shell into any pane on either host, reachable
    from a LAN NodePort that has **no human auth**, gated solely by `CLAWGATE_TERMINAL_TOKEN`.
    That is recorded as a decision, not an oversight; do not silently narrow it, and do not widen
    the exposure further without asking.
    ⚠ **The seam guard I was about to recommend ALREADY EXISTS — do not rebuild it.**
    `internal/api/terminal_write_ledger_test.go` is an AST-parsing (not grepping) invariant guard
    with its own positive control, and `terminalSurfacePrefixes` already contains `/api/term`, so
    the future route is pre-ledgered. It labels itself an invariant guard rather than regression
    coverage — an honest model worth copying. **The live lesson it encodes:** a fail-closed wrapper
    that wraps zero routes passes every test, which is exactly today's state.
    **DESIGN SETTLED 2026-09-04 — four operator decisions, each recorded with what it costs:**
    🔴 **(a) The reply SUBMITS — clawgate presses Enter.** Chosen over type-only with the tradeoff
    stated: type-only is strictly safer (a misdirected write sits there visibly instead of running)
    but makes remote answering pointless, since you would have to be at the machine anyway. **So a
    misdirected write EXECUTES.** That is the cost, accepted deliberately, and it is why (b) matters.
    🔴 **(b) Staleness is handled by RETIRING THE QUESTION, not by guarding the write — see rank
    27.** The operator redirected this from the write path to the read path. ⚠ **It shrinks the
    race, it does not remove it:** the pane signal has a 2-minute cadence, so a question can be
    visible and answerable for up to one snapshot interval after the pane moved on. **Combined with
    (a), that window is one in which a submitted reply executes into a pane that has moved on.**
    A cheap write-time guard is therefore still warranted even though the operator's fix is
    elsewhere — do not read "retire the question" as licence to skip it.
    🔴 **(c) Full audit log INCLUDING the text sent.** Timestamp, target host/pane, originating
    attention entry, exact text. Chosen over metadata-only so "what did that write actually do" is
    answerable after the fact. ⚠ **Cost, stated at decision time:** replies you type may contain
    secrets, so **this log is itself sensitive and needs credential-grade handling** — retention,
    access, and never in a public repo, a fixture, or a PR body. Redaction-on-secret-shapes was
    offered and declined, correctly: it fails silently and stores a missed secret believing it was
    scrubbed.
    🔴 **(d) Free text into ANY pane, unrestricted** — see the paragraph above. Unchanged.
    ⚠ **Provisioning `CLAWGATE_TERMINAL_TOKEN` is the step that ARMS all of this, and it is
    separable from building it.** The route can be built, merged and deployed while the surface
    stays `DISABLED (fail-closed)`; arming is then one secret away and reversible by removing it.
    Prefer that ordering — it lets the write path be reviewed and audited before it can execute
    anything, and a boot log line states which state the server is in, unconditionally, in both
    directions.
    Closing condition: a reply typed in the web UI appears in the target pane on the target host,
    verified end to end against the live pod — an API 200 is NOT the closing condition — with the
    write route provably behind `requireTerminalToken` via the existing ledger, and the write
    recorded in the audit log.
    forcing: user — the operator asked for it; held only for a design conversation, not deprioritised.

30. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#715`, squash `2d4d5cf5c`.** Claim released.
    **Originally: the reply UI component + questions-only attention-first ordering.** Repo:
    `ZacxDev/homelab-infra`. **NOT STARTED** — depends on 28 (structured options) and 29 (the write
    path). The component mounts on `/attention`, on the tmux page, and inside the `/session/{id}`
    view, so a question can be answered from any of the three.
    🔴 **OPERATOR DECISION: the tmux page's needs-attention indicator is driven by QUESTIONS ONLY.**
    Measured rationale: **34 of 36 open entries are `idle, awaiting prompt`**, so an indicator
    keyed on "any open entry" lights up nearly every window and carries no signal. Idle entries
    stay visible on `/attention`; they do not drive the tmux page.
    Closing condition: a question is answered from each of the three mounts and the entry resolves
    as a consequence, verified live.
    forcing: none

31. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#712`, squash `67d1fe4d0`.** Claim released.
    **Originally: start a new Claude Code session on a host.** Repo: `ZacxDev/homelab-infra`
    (+ the host-side agent from rank 29). **NOT STARTED — opened by the operator 2026-09-04.**
    🔴 **THIS IS THE SAME CHANNEL AS RANK 29, NOT A SECOND EFFORT — which is why it is cheap, and
    also why it is not safe until 29 is.** Measured: clawgate spawns agents as **Kubernetes pods**
    via an embedded kubeclaw Helm chart (`internal/agents/helm.go`, `embed.go`); **nothing in the
    codebase touches a host tmux server.** The only tmux write anywhere is `internal/ui/
    attention.go:196` assembling a `tmux switch-client` string for a CLIPBOARD button. So a host
    tmux `new-window` needs the rank 29 transport and nothing else new at the transport layer —
    the doc's auth section already groups `send-keys`, `kill-*` and `new-*` behind the same
    fail-closed wrapper, and `terminalSurfacePrefixes` already covers the prefix.
    🔴 **OPERATOR DECISION: it creates a window AND launches Claude Code with an initial prompt** —
    pick host, project/cwd, type a prompt, get a working session. Chosen over a bare shell, which
    was judged near-useless from a phone. **It composes with ranks 26 and 30:** a session started
    this way is immediately watchable in the chat view and answerable through the reply component.
    ⚠ **Do not confuse this with the agent-pod path.** clawgate already provisions agent pods, and
    `MEMORY.md` records that task/agent dispatch defaults to deepseek via `CLAWGATE_AGENT_MODEL`.
    This item is deliberately the OTHER thing — a real tmux session on a real host, because that is
    where the operator works. Neither replaces the other.
    ⚠ **Untouched questions, named so they are not silently decided by whoever builds it:** which
    host and how it is chosen; new window in an existing session vs a brand-new tmux session; how
    the cwd/project is picked and validated; what happens when the target path does not exist; and
    whether a spawn is rate-limited (a spawn route on an unauthenticated LAN NodePort is a fork
    bomb primitive if it is not).
    Closing condition: a session started from the web UI appears on the chosen host, running Claude
    Code on the given prompt, and is visible in the tmux read model and the chat view — verified
    live on the pod, not from a green test.
    forcing: user — the operator asked for it explicitly on 2026-09-04.

32. ✅ **DONE 2026-09-06 — `innovation-upstream/devrc#1334`, squash `5a8ec6ff`. MERGED, SHIPPED to
    BOTH hosts, and the closing condition verified live.** Claim `tmux-webapp-32` released.
    **Content-verified on `origin/main`, never by ancestry** (a squash is never an ancestor):
    `enableTmuxReplyAgent = true;` at `nix/home.nix:194`; both new guards
    (`test_the_agent_unit_IS_ARMED`, `test_the_agent_SHELLS_OUT_TO_TMUX_AND_NOTHING_ELSE`) present;
    the superseded `test_the_agent_unit_SHIPS_DISABLED` **gone**; and a nonexistent-marker grep
    returning **0** as the control that the check discriminates at all.
    **Ship read per-host, never from the final verdict:** workbench `ec8e5286 → 5a8ec6ff`
    fast-forwarded, 599 artifacts resolve / 0 dangling, `VERIFIED — on branch main at origin/main +
    switched`; laptop `580b4848 → 5a8ec6ff`, 541 resolve / 0 dangling, `VERIFIED … (clean tree) +
    switched`. The workbench's tree was DIRTY and ship classified all 5 dirty paths as untracked and
    unread-by-nix against 168 nix-read paths — i.e. what was built IS `origin/main`.
    **Closing condition met on both hosts**, with the full table in the Status section: `is-active`
    **active** / **active**, unit-file state `linked` → **enabled**, `SubState=running`,
    **`NRestarts=0`** on each, and each agent announcing a DISTINCT host scope
    (`workbench:696125`, `laptop:470158`) — the disjoint-scope property the two-agent design needs.
    🔴 **The load-bearing evidence is the ABSENCE of a `backing off` line, and it is only evidence
    because the loop logs one.** `scripts/tmux-reply-agent` logs a repeating failure condition
    exactly ONCE (`if reason != backoff_reason`), deliberately, so 17,280 ticks/day cannot bury a
    real event. A 503 "not armed", a refused credential or an unreachable server would each have
    written one line; both journals hold only their two startup lines ⇒ the polls are **answered**,
    not merely attempted. Had that guard logged every tick, or nothing, the silence would have
    carried no information at all.
    ⚠ **This closes rank 32 and nothing wider — no keystroke has been observed landing in a pane.**
    That is rank 33.
    forcing: none — closed.

33. ✅ **DONE 2026-09-06 — DRIVEN END TO END, AND IT FOUND A REAL DEFECT THAT BROKE EVERY REPLY.**
    A real `AskUserQuestion` was raised from a throwaway Claude Code session in a disposable tmux
    session (`r33`, pane `%477`) and answered by clicking in the web UI. The click was trusted, the
    confirm fired naming the target — but the write **expired undelivered**.
    🔴 **THE HOST LABEL DID NOT MATCH ON EITHER SIDE OF THE QUEUE.** The UI enqueued
    `{"host":"nixos","pane":"%477"}`; `tmux-reply-agent` polls `for writes addressed to workbench`.
    The row sat `pending` for 90 s and expired: no UI error, no agent journal line, no failed
    request. **It was not probe-specific — every reply mount on the live page emitted `nixos`,
    including a real open question of the operator's, so NO reply from ANY surface was deliverable
    on this host.**
    **Root cause:** the hook labelled the host `HOST="${CLAUDE_HOST:-$(hostname)}"` and *both
    machines are hostname `nixos`*. The laptop was unaffected only because its `settings.json`
    happens to pass `CLAUDE_HOST=laptop` — an unmanaged per-host file. The agent's own
    `local_host_label()` docstring had predicted it verbatim: *"an agent that computed a different
    label would poll for a host nobody enqueues to and deliver nothing, silently."* Both halves were
    individually correct and individually tested; only the SEAM was wrong.
    🔴 **IT CANNOT BE REPAIRED DOWNSTREAM** — `nixos` is ambiguous by construction, so no
    server-side normalisation can recover which machine meant it.
    **Fixed:** `ZacxDev/homelab-infra#735`, squash `0d553fcd`. The hook now derives the canonical
    label (`CLAUDE_HOST` → `$ACTIVITY_HOST` → `~/.config/activity-collector/env`), and falls back to
    `hostname` **deliberately** — an unresolvable label is UNDELIVERABLE, where guessing `workbench`
    would run the operator's keystrokes on the WRONG machine. `host_source` is now logged beside
    `host`. The hook runs from the clone's working tree, so a `git pull` there IS the deploy.
    **Re-verified after the fix:** `host=workbench host_source=activity-env-file`, UI enqueued
    `ui-reply:9417:workbench:%480`, agent logged `delivered s0FeuWTUOkuN0Lu5mCEsfg (pane %480)`, and
    the pane showed `● User answered Claude's questions: · rank33 verify — pick one → gamma`.
    Negative control: sibling session `r330` byte-identical before and after.
    ⚠ **A CORRECTION TO THIS ITEM'S OWN BRIEF: the `=` prefix clause belongs to a DIFFERENT PATH.**
    The reply path targets a PANE ID (`send-keys -t %480`, agent line 687) and does no name matching
    at all. `=` is used only by `new-window -t "=" + tmux_session + ":"` (line 568) — rank **31**'s
    start-a-session path, which remains unexercised.
    forcing: none — closed.

34. ✅ **DONE 2026-09-07 — closed by rank 39's PR (`ZacxDev/homelab-infra#743`, squash
    `3f6ef8ae7`, live as `0.8.29`). Read rank 39 for the evidence; they were one item.**
    The residual text is kept below because it is the accurate statement of what was
    wrong, and the fix is only legible against it.
    **Decide the two residuals the audit recorded as ACCEPTED — NOW THE LOAD-BEARING ITEM.** The
    browser tier **authenticates nobody** (`requireSession` is a literal `return next`), and the
    host agent sends an **execution-grade token over plain HTTP** to the LAN NodePort every ~5 s.
    🔴 **TWO CHANGES ON 2026-09-06 MADE THIS STOP BEING A RESIDUAL.** `#738` put tool INPUTS —
    file paths, bash command lines, edit bodies — on that page, reversing a written refusal whose
    stated reason was "this view is rendered to a page on an unauthenticated LAN surface". `#741`
    added a free-form `send-keys` box to the same page. So the surface now both READS the contents
    of the operator's sessions and WRITES arbitrary commands into them, with no human auth.
    Closing condition: the browser tier authenticates somebody, or the surface is disarmed.
    ✅ **MET by authenticating** (not by disarming): `requireSession` and
    `requireArmedTerminalUI` both require an HMAC-signed session cookie as of 0.8.29.
    forcing: none — closed.

35. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#737`, squash `efa44e763`.** UI feedback round,
    PR 1 of 3: `internal/ui` only. Content-verified on `trunk`.
    Wider shell (`2xl` 96→110rem plus a new `min-[2560px]` 2400px step) with the old cap
    **RELOCATED, not deleted** — `proseWidth()` caps sentences at 70ch on their own element, and
    `TestProseBlocksAreCappedIndependentlyOfTheColumn` asserts the column does NOT carry it. tmux
    cards became an auto-fit grid with a 32rem minimum track (1 column on a laptop, 4 on the 3440).
    Chat prose routes through the pre-existing `renderMarkdown`; the truncation notice now explains
    itself and is pinned as a WHOLE normalised string.
    🔴 **THE AUTO-APPROVE FOLD REVERSED RANK 20 KNOWINGLY, ON ONE CONDITION.** The loud
    "auto-approve ALL is ON" bar moved into the header dropdown; what pays for it is a THIRD header
    face (`aaToneGlobal`, rose) distinct from the amber project-armed face, wired server AND client.
    The coupling that made this dangerous was in the `all` chip's own comment — it justified hiding
    on phones *because the loud in-flow bar was on screen*. That assertion now checks the rose face
    and asserts amber is ABSENT, because `data-aa-active` is equally true of one armed project.
    Three guards FLIPPED in place, keeping the old `aaHidingIdioms` predicate wholesale.
    forcing: none

36. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#738`, squash `a74f312f7`.** PR 2 of 3: a tool's
    input, collapsed. The payload was dropped at PARSE time by a written refusal, quoted verbatim in
    the code that reverses it; the guard `TestAToolCallNeverCarriesItsInput` was INVERTED in place.
    Tool RESULTS are still refused — the input is what the agent ASKED for, the result is a machine
    answering in the operator's voice.
    ⚠ **A PREMISE IN THE BRIEF WAS WRONG AND IS CORRECTED IN THE PR:** the per-record cap does NOT
    protect the stored tail. `MaxTailBytes` is enforced on INGEST against the raw JSONL, upstream of
    the parser, so an oversized record has already spent that session's tail whether or not it is
    displayed. The cap (`maxToolInputChars = 4000`) is a PAGE-WEIGHT bound.
    forcing: none

37. ✅ **DONE 2026-09-06 — `ZacxDev/homelab-infra#741`, squash `9d4b6900`.** PR 3 of 3: a free-form
    reply on the session page, EntryID 0. Mostly a MOUNT — `ReplyView.EntryID` was written
    zero-safe for it, and the target line, `hx-confirm`, idempotency key and a browser-tier rate
    limiter all already existed.
    🔴 **`pane_id` WAS ALWAYS ON THE WIRE AND WAS NEVER DECODED** — the pusher is "deliberately a
    dumb pipe" posting `session-manager --json` verbatim. ⚠ `LEAN_ROW_FIELDS` does not list it, nor
    `pane_preview` which has rendered for months: LEAN is a different VIEW, not the wire contract.
    Host and pane come from ONE row and ambiguity resolves to NOTHING, because a pane id is unique
    per tmux SERVER.
    🔴 **THE BUG THE UNIT TESTS COULD NOT SEE:** `chatViewFor` returns a FRESH `ChatView` literal on
    the success path, so fields must be copied by name. `Question` was, `Reply` was not — the
    control rendered only for sessions with NO stored transcript. The seam test passed over it
    because its server had no transcript store, i.e. it exercised the early return, the one path
    that was never broken. Add a field to `ChatView` ⇒ add it to that literal.
    forcing: none

38. ✅ **DONE — SHIPPED 2026-09-06 as `0.8.28` (deploy commit `ae534d56f`), and the closing
    condition was met on 2026-09-07 when rank 34 was re-read against the live surface.**
    🔴 **THE HEADLINE BELOW IS NO LONGER TRUE AND IS KEPT ONLY AS THE RECORD OF WHY THIS ITEM
    EXISTED.** "NONE OF RANKS 35–37 IS LIVE" was correct on 2026-09-06 and FALSE from the deploy
    that same evening; it sat in this doc as an unqualified 🔴 for a day, which is exactly the
    rot this file warns about everywhere else. Re-measured 2026-09-07 against the running pod:
    the wider shell, the auto-fit tmux grid, the collapsed `<details data-tool-detail>` and
    `data-chat-freeform-reply` are all present, each with its pre-change marker at **0** as the
    negative control. **The mechanism it documents is still true and still load-bearing** — see
    the next paragraph — so read the paragraph, not the headline.
    ⚠ **THE MECHANISM, WHICH HAS NOT CHANGED: a merge to `trunk` deploys NOTHING here.** clawgate
    has no ImagePolicy/ImageUpdateAutomation; shipping is four deliberate steps — build the image,
    push to harbor, bump the pin, commit for Flux. Confirmed again on 2026-09-07 shipping `0.8.29`,
    and it is why `#747` (a log-string fix, merged with no pin bump) is on `trunk` and NOT running.
    **Measured 2026-09-06 after all three merged:** `clawgatectl health` read **0.8.27**, while
    `clusters/workbench/apps/clawgate/deployment.yaml` pins
    `harbor.homelab.lan/library/clawgate:0.8.27`. There is no ImagePolicy/ImageUpdateAutomation for
    clawgate — a merge to `trunk` changes NOTHING about what is running. Shipping is four deliberate
    steps: build the image, push to harbor, bump the pin, commit for Flux.
    🔴 **THE DEPLOY IS WHAT ARMS #741** — a free-form `send-keys` box on an unauthenticated page. It
    is therefore a separate decision from the merges, and rank 34 is the thing that would make it
    safe rather than merely authorised.
    ⚠ **Do not read `clawgatectl health` as a deploy check for these** — the version moved to 0.8.27
    for unrelated reasons while all three PRs sat unmerged. A version that CHANGED is not evidence
    that YOUR change shipped; verify by CONTENT against the running pod.
    Closing condition: the operator decides to ship or not; if shipped, the tool-detail disclosure
    and the free-form box are observed live and rank 34 is re-read against them.
    ✅ **MET, both halves.** Shipped: `0.8.28`, pin `ae534d56f`. Observed live 2026-09-07:
    `data-tool-detail` and `data-chat-freeform-reply` present on real session pages, with
    `data-reply-state="ready"`. Rank 34 re-read against them and CLOSED the same day by
    authenticating the tier (`ZacxDev/homelab-infra#743`, `0.8.29`) rather than disarming — the
    operator's explicit call, made with the measured exposure in front of them.
    forcing: none — closed.

39. ✅ **DONE 2026-09-07 — `ZacxDev/homelab-infra#743`, squash `3f6ef8ae7`, LIVE as `0.8.29`.**
    Closes rank 34 as well; they were one item. The browser tier now authenticates.
    **Verified by reproducing the ORIGINAL SYMPTOM against the live pod, not by
    reading a rollout.** Before → after, same request each time:
    | probe | 0.8.28 | 0.8.29 |
    |---|---|---|
    | uncredentialed `POST /ui/term/send-keys` | **200** `{"created":true,"tier":"browser"}`, host agent ran it ~3 s later | **401** `not signed in` |
    | anonymous `GET /` | **200**, 147 KB dashboard | **303** → `/login` |
    | anonymous `GET /session/<id>` | **200** with tool inputs + reply box | **303** → `/login?next=…` |
    | correct password | — | 303 + cookie → dashboard **200**, 147,762 B, transcript renders |
    | wrong password | — | **401**, no cookie issued |
    | `/health`, `/metrics` | 200 | 200 (kubelet + Alloy untouched) |
    🔴 **THE DESIGN POINT, AND THE ONE A REVIEWER WILL GET WRONG: THE GATE HAD TO
    LAND ON TWO WRAPPERS.** `/ui/term/send-keys`, `/ui/term/new-session` and
    `/ui/term/launch` are wrapped in `requireArmedTerminalUI` and **never pass
    through `requireSession`**. Measured by removing ONLY the terminal-UI check:
    the shell and transcript guards stayed **PASS** while send-keys returned
    **200**. Gating `requireSession` alone ships a live remote shell behind a
    green suite. `TestEveryBrowserSurfaceRequiresAHumanSession` reads the route
    table from source and fails when the open-route ledger GROWS or SHRINKS.
    **Fail-closed**, deliberately not the enforce-when-set shape
    `requireHookToken` uses: unset or <12 chars ⇒ the browser tier refuses.
    `optional: true` on the env is the *safer* failure (pod healthy, machine tiers
    alive, `/login` names the missing var) rather than a
    `CreateContainerConfigError` that takes `/health` down. Rotating
    `CLAWGATE_UI_PASSWORD` invalidates every outstanding cookie — the signing key
    is derived from it, and that is the only revocation this design has.
    🔴 **THE MACHINE TIER IS UNTOUCHED AND THAT WAS PROVEN, NOT ASSUMED.** Both
    host agents `active`, **0** `backing off` lines, and a bounded control write
    enqueued through the token tier to pane `%999998` was claimed and attempted by
    the workbench agent within seconds. "No log lines" alone would have been
    equally consistent with a dead agent.
    🔴 **CI FOUND 15 REAL BREAKS MY LOCAL GREEN DID NOT** — not flakes, and not
    the rank-17 signature: 10 of 14 in one file, each naming the same cause.
    **Node's global `fetch()` has no cookie jar**, so every helper arming
    auto-approve through a raw fetch went anonymous; and `task-sse-regroup` builds
    three of its OWN contexts, which the auto-use `signIn` fixture does not reach.
    Fixed by sweep: `ServerHandle.uiFetch()`, and every `fetch(${baseURL}…)` path
    in e2e classified against the Go route table by wrapper. Second round:
    **193 tests, 0 failed**, all four checks green on `ad72a6bc9`.
    ⚠ **Two build-path defects found that NOTHING errors on.** `tailwind.config.js`
    scanned `internal/ui` only, so the login page (in `internal/api` by design)
    would have shipped **unstyled** — control: `bg-emerald-600` in `app.css` went
    **1 → 0 → 1**. And the Dockerfile's css stage copies only what it is told to
    while Tailwind **exits 0 on a missing content path**, so the *image* would ship
    unstyled while a dev box looked correct. A package-wide glob was tried and
    rejected on measurement: it scans `_test.go` and shipped `.[project:foo-bar]`,
    `.[logStart:logEnd]`, `.[project:clawgate]` into `app.css`.
    ⚠ **Two tests asserted the hole and were rewritten**, not deleted:
    `TestOpenRoutesNoAuth` listed `/` as open, and `login.spec.ts` was three specs
    named *"open access (no human auth)"* including *"the removed /login route is
    not registered (404)"*.
    ⚠ **`signedIn()` masks the gate at 82 test sites ON PURPOSE**, so
    `browser_auth_test.go` builds its handlers WITHOUT it — its first version used
    the shared helper and **passed 200 against the very defect it was written to
    catch**.
    ⚠ **NOT DONE, and not live:** the boot line
    `clawgate <ver> listening ... (auth: hook token only; UI open behind Authelia /
    trusted LAN)` was FALSE from 0.8.29 and is fixed in `main.go` on trunk — but a
    log string only changes with a new image, so **the running 0.8.29 still prints
    it**. Harmless (the three authoritative tier lines print directly above it and
    are correct), and it ships with the next build.
    ⚠ **`/audit-pr` was offered twice and NOT run** — operator chose to merge on
    the green re-run. Recorded, not hidden.
    forcing: none — closed.

40. ✅ **DONE 2026-09-07 — both merged.** `#1353` (pre-deploy handoff) landed as
    `5cc8d32f`; `#1350` (UI briefs) landed as `413d9bb87`, both devrc gates green
    (nodetests 1449/1449, pytests 21973 passed), content-verified on `main`
    (`claudedocs/briefs-clawgate-ui-2026-09-06.md` present).
    ⚠ **THIS ITEM WAS SILENTLY SKIPPED FOR MOST OF A SESSION, AND THAT IS THE
    REUSABLE PART.** The 2026-09-07 session took rank 34/39, then 41, and never
    touched 40 or said it was skipping it — the ranked list was read for the item
    being worked, not swept for what else was already actionable. `#1350` had been
    sitting green and mergeable the whole time. **Taking the lowest-numbered OPEN
    item is the rule; noticing that a cheap one is ALSO open costs one
    `gh pr list`.**
    forcing: none

41. ✅ **DONE 2026-09-07 — EXERCISED AGAINST REAL tmux, and the control reproduced the
    hazard on the live server the same minute.** No code change: the `=` prefix is
    correct as written. What was missing was evidence, and it now exists.
    🔴 **THE DISTINCTION THAT MADE THIS WORTH DOING — `open_window`'s docstring
    already records measurements, and they are measurements of TMUX, not of the
    AGENT.** They establish that `-t scratch2:` prefix-matches onto `scratch20`
    and that `-t =scratch2:` does not. They say nothing about whether the agent's
    own `open_window()` builds and uses that target correctly, which is the code
    path the start-a-session feature actually takes. This drove that function.
    | probe | result |
    |---|---|
    | raw `new-window -t <SHORT>:` (no `=`) | **rc 0, landed in `<LONG>`** — the wrong session |
    | agent `open_window(cwd, <SHORT>)` | **refused**, `can't find session`, pane `''` |
    | agent `open_window(cwd, <LONG>)` | pane `%61`, **`display-message` confirms it is in `<LONG>`** |
    | windows in the probe session | 1 → **3** exactly, so the refusal created nothing |
    | teardown | 22 sessions before, **22 after**, no strays |
    **The first row is the load-bearing one.** Without it, "the agent refused" is
    equally consistent with a tmux that would have failed anyway, and the `=`
    prefix would be proven to do nothing. The unguarded spelling silently
    succeeding into the wrong session is what makes the guarded one meaningful.
    **The third row is the other control:** a refusal alone is equally consistent
    with an `open_window()` that is broken for every input.
    🔴 **THE PROBE BUILT ITS OWN AMBIGUOUS PAIR RATHER THAN USING `scratch2`/`scratch20`.**
    Both of those now EXIST on the live server, so `-t scratch2:` resolves
    exactly and the hazard is NOT reachable through them today — this item's own
    text ("the live server has `scratch2` beside `scratch20`, so the hazard is
    reachable") had gone stale. The hazard needs a name that is a strict PREFIX of
    a live session and is itself ABSENT; the probe created `zzr41probe20` and
    targeted the absent `zzr41probe2`. Detached throwaway sessions only, none of
    the operator's targeted, nothing attached, so no window was raised.
    ⚠ **One trap hit and worth recording:** the first run copied the agent to
    `/tmp/tra.py` and it died on `FileNotFoundError: /tmp/lib/tmux_text_policy.py`
    — the agent loads a sibling `scripts/lib/` module at import time. That is the
    repo's own documented "a script pulled out of a ref arrives without its
    sidecar" rule, walked into anyway. Run it from a worktree, not a copy.
    forcing: none

43. ✅ **DONE 2026-09-12 — RESUMED.** `lastAppliedRevision` June `c8faceea` -> `5970433e`,
   `Ready=True`, both live agents survived, pod rolled onto
   `harbor.homelab.lan/library/openclaw-image:2026.6.11-py-cg0.8.31`. Prune risk was re-measured
   AT THE MOMENT OF ACTION, not from the earlier note: 36 agent dirs deleted from git during the
   suspension, ZERO with a live namespace; both surviving devpod namespaces confirmed still
   declared in git first. This closed rank 43 and task 375 together.
   forcing: incident — a merged PR (#769) was silently inert in production since June.

44. ✅ **DONE 2026-09-12 by `devrc#1561`** (a DIFFERENT session), squash-merged 03:16:37Z. Not the
   one-line ledger addition this item proposed — `_is_prose_only()` exempts `claudedocs/` from
   BOTH scanners, removes 11 dead entries, and adds a positive control that executable offenders
   are still caught. Verified green in a clean worktree at `origin/main`: the three target tests
   pass, and the whole `test_guard_core.py` module is 1537 passed. See rank 50.
   forcing: gate — red on `main`, so it surfaced in any devrc PR's pytests leg.

46. ✅ **DONE — `ZacxDev/homelab-infra#797` MERGED 2026-09-11T23:39:55Z**, 2.3 days before the
    sweep that evicted this entry. The verbatim text below still reads `4 checks pending. IN
    FLIGHT`; that was true when written and is the reader's only evidence otherwise.
    ORIGINAL, verbatim:
    46. **Land #797** (homelab-infra, `containers/clawgate/internal/ui/`) — 4 checks pending.
       IN FLIGHT: ZacxDev/homelab-infra#797.
       forcing: none

50. ✅ **DONE 2026-09-12 — #1549 CLOSED UNMERGED at 05:00:00Z, superseded by #1561.**
    🔴 Closed by a DIFFERENT concurrent session on the operator's instruction, one minute before
    this session's own analysis comment landed at 05:02:38Z — two sessions reached the same
    verdict independently and the second was wasted work. It survived `claim-work` because the
    two sides held DIFFERENT slugs for one job (`ci-flakes-and-misattribution-11` vs
    `devrc-kill-ledger-scope-executables`); the lock only catches a duplicate when both derive
    the same slug. Original finding below, kept because the measurement stands.
    ORIGINAL: #1549 is SUPERSEDED BY #1561, do NOT land it. #1561 merged
    03:16:37Z and fixes the same defect more completely (one predicate consulted by BOTH scanners
    vs #1549's one; 11 dead entries removed vs 2; plus a positive control). #1549 meanwhile went
    `CONFLICTING/DIRTY` and its three gates are `KILLED` (gate pod died — not a code failure), so
    its red says nothing about its content. The one real difference — #1549 kept the shell-text
    scanner reading `claudedocs/` — does not matter: the runtime hook still denies the command
    (measured, with a negative control). Comment posted on the PR recommending closure; see
    rank 52 for the one remaining click.
    forcing: gate — the ledger red-lined `main` three times in one day; #1561 closed that.

51. ✅ **DONE 2026-09-12 — #1515 MERGED**, squash `cf77128d`, verified by CONTENT
    (`claude/skills/clawgate/reference/cross-session-reach.md` present on `origin/main`), not by
    exit code. 🔴 It went red ONCE more first, on
    `test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger` — a STALE BASE by seven
    minutes: the branch was refreshed at 00:00:22Z and #1567, which fixes exactly that test,
    merged at 00:07:05Z. Confirmed with `merge-base --is-ancestor`, then refreshed again as
    `571149cb` and green. History below.
    ORIGINAL: UNBLOCKED and pushed; waiting on CI only. Its only red was
    #1549's subject, which #1561 fixed on `main`. Merged current `main` into the branch (no
    force-push) and pushed as `ae8f79d7`; it is now `MERGEABLE / CLEAN` with 3 Tekton checks
    running. Verified locally first on the merged tree: `-k kill` 96 passed, full
    `test_guard_core.py` 1537 passed. 🔴 #1515 adds a file under `claude/skills/`, NOT
    `claudedocs/`, so #1561's exemption does not cover it — checked explicitly for `kill-s` text
    and there is none. IN FLIGHT: innovation-upstream/devrc#1515.
    forcing: none

52. ✅ **DONE — see rank 50; another session closed it at 05:00:00Z.**
    ORIGINAL: Close `devrc#1549` unmerged. Superseded by #1561 (rank 50 carries the measurement and the
    PR comment is already posted). Nothing in it needs porting. One click; left for the operator
    because closing was not what was asked for.
    forcing: none

55. ✅ **DONE 2026-09-12 — THE REPLY WAS DRIVEN, AND IT FOUND A DEFECT.** A reply typed in the
    web UI reached a real tmux pane and the shell executed it (`%77` shows the probe text then
    `scratch: command not found` — typed **and** Enter pressed). Card 517's criteria 2 and 3 are
    now measured LIVE rather than in a fake; criterion 1 holds for its first hop and **fails for
    its second**. Recorded as comment **1314 on card 517**; run report (gitignored, so not the
    durable copy) `.opencode-dispatch/tmux-ui-verify/findings-run4.md`.
    🔴 **THE DEFECT — ONE REFETCH, AND IT CAN FIRE BEFORE THE OUTCOME EXISTS.** `#panel-attention`
    refetches on `load, sse:attention.changed, clawgate:resync, clawgate:termwrite from:body`, and
    **nothing broadcasts when the host agent claims or completes a write** — so the POST triggers
    exactly ONE refetch and the final screen is decided by a race. Reply to a real pane went
    terminal at **+2.65s** *after* its refetch at **+1.17s** → froze on `queued` for a full 50s
    sample and only read `sent` after a reload. Reply to a nonexistent pane went terminal at
    **+0.17s** *before* its refetch at **+1.79s** → rendered the true `failed`. The timings make
    **the broken side the normal one** (healthy agent ~2.6s vs refetch ~1.2s), and the frozen text
    is not stale but FALSE: *"Queued, not typed yet … workbench's agent has not picked it up"* for
    a reply that landed 1.5s later. See rank 58.
    🔴 **NO TEST CAN SEE IT, STRUCTURALLY:** e2e has no host agent, so `queued` is terminal there
    by construction and `reply-delivery.spec.ts:106-110` asserts exactly that, correctly; the Go
    tests move the row in a fake. #754's own comment listed *"No host-side agent was involved
    anywhere"* under NOT-verified. This is that gap — the isolation seam, both halves green alone.
    🔴 **TWO HARNESS FACTS THAT BLOCK ANY REPEAT, neither known to the brief.** (a) **An entry
    raised with the DEFAULT `--session` is invisible on EVERY surface** — `QuestionIsStale`
    (`internal/attention/staleness.go:182`) suppresses a question whose session's pane was observed
    after the entry and is not waiting, and the raising session is busy by definition. The first
    entry (13301) rendered nowhere for this reason. Raise with
    `env -u CLAUDE_CODE_SESSION_ID … --session ''`. (b) **`/tmux` joins questions to windows by
    `SessionID`, NEVER by pane** (`api/tmux_ui.go:244`), so a session-less entry cannot render
    there at all — **the `/tmux` mount stays UNEXERCISED, and is unreachable with a disposable
    pane**. The axis was driven on `/attention`, same control, same `ReplyDeliveryState`.
    🔴 **`hx-confirm` DEFEATS A TRUSTED CLICK FROM A BACKGROUND TAB, and fails SILENTLY.** The
    first attempt enqueued NOTHING — 0 `/ui/term` requests against **25 `GET /ui/attention`** as a
    positive control — because the panel re-renders the control server-side and REVERTED the
    attribute mutation before the click 236ms later. Fire set-value + drop-confirm + `.click()` in
    ONE synchronous expression. NOT verified as a result: the trusted-click path, `sending`, and
    `unknown`. The old brief is spent — its ids (entry `13178`, pane `%75`) were already dead.
    forcing: none — the measurement was taken.

58. ✅ **DONE 2026-09-14 — FIXED, MERGED AND VERIFIED IN PRODUCTION.** `ZacxDev/homelab-infra`
    **#813** (squash `1a3bdef6`) + **#817** (squash `5890f3b9`), shipped as **clawgate 0.8.34**
    (pod `clawgate-5b945c79fb-rcmf6`, `1/1`, 0 restarts, `health` → `0.8.34`).
    🔴 **THE CLOSING CONDITION BELOW WAS MET CLAUSE BY CLAUSE, on the live pod**: scratch pane
    `%102`, entry `14440` raised with `--session ''`, reply submitted through the real UI, and
    `data-reply-state` observed by a MutationObserver installed BEFORE the click —
    `ready` → **`queued` @ +580 ms** → **`sent` @ +6033 ms**, stable through +32.3 s, **no reload**.
    The queue row was `claimed` @ +5.42 s and `completed` @ +5.48 s (`delivered`, pane `%102`), so
    `completedAt` is later than the POST as the condition requires. The text ARRIVED: the pane shows
    `scratch 0834 delivery check` then `scratch: command not found` — typed AND submitted.
    🔴 **Why this is not a case that would have passed anyway:** the terminal transition landed at
    **+5.4 s**, far beyond the ~1.17 s single post-POST refetch that was the panel's ONLY read
    before the fix. Under 0.8.32 this exact timeline froze on `queued`.
    **The fix:** an SSE `termwrite.changed` broadcast from the claim and result handlers, with three
    surfaces subscribing — 6 effective payload lines (1 const, 2 broadcast calls, 3 `hx-trigger`
    edits). Audited: Round 0 split the original PR; Round 1 returned safe-to-merge with its own
    8-mutant battery confirming both broadcasts die individually for their own reasons.
    ⚠ **RETRACTION — the sentence below claiming "a test for it cannot live in e2e as it stands"
    was MINE AND IT WAS FALSE.** It restated a fact about the FIXTURE ("e2e has no host agent") as a
    fact about the HARNESS. `e2e/tests/reply-delivery.spec.ts` already sets `CLAWGATE_TERMINAL_TOKEN`
    and already makes credentialed POSTs, so **the spec can play the host agent itself** — and
    `e2e/tests/task-sse-regroup.spec.ts` already existed to close this very seam class, opening
    multiple browser contexts and guarding both rival explanations. #813 added exactly that leg: it
    drives the real `claim`/`result` routes and was watched RED in two arms (claim suppressed →
    `Expected "sending", Received "queued"` — the production symptom verbatim; result suppressed →
    `Expected "sent", Received "sending"`, proving the two broadcasts are independently pinned).
    Do not re-derive the retracted claim.
    ⚠ **What is NOT closed:** the axis still sticks when the host agent **DIES**, because
    `pending`+stale → `failed` (90 s TTL) and `claimed`+stale → `unknown` (10 min grace) are
    TIME-driven, nothing broadcasts them (correctly — no row moves), and `#panel-attention` has no
    poll. The session page (30 s) and tmux grid (60 s) self-heal; the attention panel does not.
    Pre-existing, not a regression, and the post-fix frozen text (`sending`) is the safer error —
    but "stop the axis freezing" closes one half of the class, not both.
    ORIGINAL: 🔴 **The reply control freezes on `queued` after a SUCCESSFUL delivery — fix the
    missing broadcast.** Found by rank 55's drive; full measurements there and in card 517 comment 1314.
    The row transitions to `delivered` in the claim/complete handlers and **nothing tells the
    browser**, so `#panel-attention`'s single post-POST refetch is the only read and it usually
    fires first. Fix direction: broadcast `clawgate:termwrite` (or an SSE `termwrite.changed`)
    from the handlers where the row actually transitions, or poll while any rendered delivery is
    non-terminal. ⚠ A test for it cannot live in e2e as it stands — e2e has no host agent, so the
    fixture must move the row *after* the POST's refetch, which is the case no current test
    builds.
    CLOSING CONDITION: on an armed deployment with a live host agent, a reply into a disposable
    pane reaches `data-reply-state="sent"` **without a reload**, with the queue row's
    `completedAt` later than the POST. Checked by: re-running rank 55's drive (the harness recipe
    is in that rank, including the two facts that make the entry render at all).
    forcing: gate — card 517's criterion 1 names "visible without a page reload", and the hop
    that matters on a healthy host does not satisfy it.

61. ✅ **DONE — `ZacxDev/homelab-infra#819` MERGED 2026-09-14T04:51:27Z** (*"prefer the burst
    node — the trunk TaskRunTimeouts were node I/O, not a code defect"*). Its closure was
    recorded at the time as a `### ✅ RESOLVED` heading in the doc's **Open investigations**
    section, not in this rank body — the third-location case in the table above. The verbatim
    text below still reads as a live red gate.
    ORIGINAL, verbatim:
    61. **`tekton/clawgate-e2e` is red on `trunk` — unbreak it or stop gating on it.** See the open
        investigation above. A permanently-red gate trains everyone to click through, and this one has
        already been merged past once this session (#817, knowingly, on `clawgate-ci` green).
        forcing: gate

62. ✅ **DONE — `innovation-upstream/devrc#1660` MERGED 2026-09-14T03:01:03Z**, ~3 h before the
    sweep. The verbatim text below still reads `open, mergeable … Unmerged, the retraction is
    invisible to the next session` — it is merged, and the retraction landed.
    ORIGINAL, verbatim:
    62. **Merge devrc #1660** (open, mergeable) — it records rank 58's closure and retracts the
        fixture-vs-harness claim. Unmerged, the retraction is invisible to the next session.
        forcing: none

18. ✅ **DONE — `ZacxDev/homelab-infra#820` MERGED 2026-09-14T15:56:37Z.** 🔴 Its title says
    *"the recorded root cause has INVERTED"*, so the diagnosis in the verbatim text below is
    RETRACTED by the PR that closed it — read `#820` before trusting any mechanism stated
    here. The `Closing condition:` paragraph below is satisfied, not open.
    ORIGINAL, verbatim:
    18. **`clawgate-ci`'s `go` leg reds on POSTGRES-BACKED tests under contention — a SIBLING of 17,
        deliberately not folded into it.** Repo: `homelab-talos`, `containers/clawgate/internal/store/`
        and `cmd/clawgatectl/`. 🔴 **Different leg, different mechanism, different closing condition:**
        17 is an ephemeral server missing a 15s HEALTH-CHECK budget in `clawgate-e2e`/`ux-audit`; this
        is `go test` itself timing out against Postgres inside `clawgate-ci`. Merging them would give
        one item two closing conditions, and neither would ever be checkable.
        **Measured 2026-09-02, and the discriminator is that the FAILING TEST MOVES:**
        | PipelineRun | revision | failed |
        |---|---|---|
        | `clawgate-ci-btr4h` | `20a277d7` (not mine) | `TestSeamClientToServerMovesTheThreadCount` (30.03s), `TestDeleteSucceedsWhenArchiveFails` (10.35s) |
        | `clawgate-ci-vrpc4` | `ea98254a` (rank 11) | `TestSweepArchivesEveryUndecidedRowInABatch`, on `pgstore: sweep iterate: timeout: context deadline exceeded` |
        Three tests, two packages, two revisions, ~100 minutes apart, all timeout-shaped — and
        `internal/ui`, the ONLY package rank 11's diff touched, PASSED in that same run (5.291s, 94.8%
        coverage). Same family as devrc's diagnosed store-api fsync contention.
        🔴 **THE DEV-HOST TIER IS STRUCTURALLY BLIND TO THIS, SO A LOCAL GREEN IS NOT A REBUTTAL.**
        Measured, not assumed: `go test ./internal/store/ -run TestSweepArchivesEveryUndecidedRowInABatch`
        prints `--- SKIP` with *"set CLAWGATE_TEST_DATABASE_URL to run the Postgres-backed
        request-history tests"*. A local `20 ok / 0 FAIL` therefore says NOTHING about these tests, and
        quoting it as though it did is the two-tier error this repo already documents.
        Closing condition: a red on the `go` leg can be attributed to a diff without a re-run — the
        store tests get their own Postgres with a bounded startup, or the failure names the contended
        resource. Until then, read WHICH test failed and check whether it moved between runs before
        debugging the diff.
        🔴 **ROOT-CAUSED 2026-09-04 BY ANOTHER SESSION — DEVICE-ISOLATED, NOT CONTENTION IN GENERAL.**
        Landed on `homelab-infra` `trunk` as `eff01a8f0` + its follow-ups: `clawgate-ci` is
        **0-pass / 14-fail on node `talos-uvh-gtj`** against 3-pass elsewhere, and that node's system
        disk (a Crucial M500) does **~90 ms per 4 KB fsync against ~1.5 ms**, i.e. ~59× slower. So the
        discriminator is now the NODE, not just "did the failing test move". **Read the PipelineRun's
        node before debugging a `go`-leg red.** Corroborated independently here on 2026-09-04:
        `#680`'s `clawgate-ci` red had `step-go` **exit 0** with only `cmd/clawgatectl` failing on
        `canceling statement due to statement timeout` during migrate, on that same node, while
        `internal/api` and `internal/ui` both reported `ok` in the same run.
        ⚠ This does NOT close the item — the closing condition is about attribution being possible
        without a re-run, and that work is owned by the session that did the diagnosis. It is recorded
        here so the next reader stops re-deriving the mechanism.
        🔴 **THIRD INSTANCE, 2026-09-07, AND IT IS THE CLEANEST PAIR YET — SAME REVISION, OPPOSITE
        VERDICTS.** On `#747`, a **9-line log-string diff** touching only `containers/clawgate/main.go`:
        | run | revision | verdict |
        |---|---|---|
        | `clawgate-ci-b6ql9` | `020e28a1ee9ea…` | **Failed** |
        | `clawgate-ci-rerun-6j6m5` | `020e28a1ee9ea…` — *byte-identical* | **Succeeded** |
        Failing set was `TestFlagIdleWritesOnceUnderConcurrency` (14.23s),
        `TestRequestHistorySurvivesDelete` (13.86s), `TestSweepArchivesExpiredRequests` (14.59s), plus
        the `hook` leg's `not ok 39` — a bats case that timed out after **5s waiting on a detached
        child**. Node was `talos-xr6-r7p`, i.e. **NOT** the known-bad `talos-uvh-gtj`, so the
        device-isolated reading does not cover this one.
        🔴 **THE DISCRIMINATOR THAT WORKED WAS WALL TIME, AND SPECIFICALLY *WHOSE* TIME MOVED.** The
        whole run inflated — `internal/notes` **92.8s** and `internal/store` **48.0s** against
        sub-second locally, top figure 111s — which is load, because a failed assertion inflates
        exactly one test. Reading that before touching the diff is what turned a scary red on a
        security-adjacent PR into a 90-second question.
        **The re-run recipe, since it is now used often enough to be routine:** take the failed
        PipelineRun's own `spec` (it carries `params.revision`), strip `tekton.dev/*` labels, give it
        `generateName: clawgate-ci-rerun-`, and `kubectl create` it. A green re-run on the IDENTICAL
        revision completes the attribution; a red one on the SAME tests refutes the contention reading.
        forcing: gate — with 17 this makes three of the four clawgate checks capable of reds that are
        not about the change, and this one is the worst of the three to dismiss: unlike 17 it can fail
        on a package a Go diff genuinely touches, so "it is just the flake" will eventually be wrong.

42. ✅ **DONE — closed by `ZacxDev/homelab-infra#749`, squash `6ee01514c`**, "fix(clawgate): close
    rank 42's three residuals — two guards walkable by a spelling, one banner carrying the DSN".
    Verified 2026-09-14 against `origin/trunk` (the ref, not a working tree) and by RUNNING the
    guards, not by reading them. 🔴 **The FILTER is the load-bearing datum, not the count** — a
    filter that matches nothing also prints `ok`, so a bare total is unreproducible and an earlier
    draft of this entry quoted one ("20") that no filter reproduces. From
    `containers/clawgate`, `go1.25.14`:
    ```
    go test ./internal/ui/ ./internal/api/ -count=1 -v -run \
      'TestAaHidingStylesSeesInlineStyles|TestAaEvictsInSeesEveryWayAWindowCanDie|TestPersistNoticeRedactsTheDSNAndLogsItInFull|TestPersistNoticeRendersTheSENTINELsTextNotTheWRAPPERs|TestEveryPersistNoticeIsLogged'
    ```
    → **5 `--- PASS`, 0 FAIL, 0 SKIP** — one per named guard, counted from `--- PASS` lines rather
    than trusted from `ok`. An independent audit round additionally **mutation-killed** all three:
    forcing `aaOffScreenScale`'s unknown-unit fallback to `return false` reds the `left:-7331nsu`
    row; reducing `aaIsMapsPkg` to `name == "maps"` fails three alias rows AND the reverse row
    `import maps "encoding/json"`; returning `err.Error()` from `autoApprovePersistDetail` reds
    `TestPersistNoticeRedactsTheDSNAndLogsItInFull`.
    Each residual has a named successor in the source:
    - `aaOffScreen` px-only → `aaOffScreenScale` + `aaPxPerUnit` + `aaViewportUnits` convert every
      absolute, font-relative and viewport unit, and an UNRECOGNISED unit falls back 1:1 rather than
      being skipped — the fail-closed direction. Its own docstring now names the old hole
      ("This arm used to require a `px` suffix"). Control: `TestAaHidingStylesSeesInlineStyles`.
    - `aaEvictsIn`'s literal `maps` match → `aaIsMapsPkg(name, imports)` resolves the package
      through the FILE's own import block, so an alias and a dot-import are both hits. Its control
      `TestAaEvictsInSeesEveryWayAWindowCanDie` carries alias rows that differ from their un-aliased
      twins in nothing else, so a predicate ignoring imports must fail one of each pair.
    - `autoApprovePersistNotice` embedding `err.Error()` → `autoApprovePersistDetail` returns a
      CLASSIFICATION, gated by an ALLOWLIST of sentinels (`context.DeadlineExceeded`/`Canceled`)
      rather than a scrub — deliberately, because a scrub of host/port/user patterns would be a
      guard on WORDS, correct for one driver wording and walked by the next release. The detail is
      MOVED, not lost: `(*Server).persistNotice` logs the error in full, and
      `TestEveryPersistNoticeIsLogged` fails if a fourth toggle calls the builder directly.
    ORIGINAL: **Three residuals the UI round left disclosed rather than fixed.** `aaOffScreen`
    understands only `px` offsets; `aaEvictsIn`'s `maps` arm matches the literal
    package name so an aliased import evades it; `autoApprovePersistNotice` embeds
    `err.Error()` verbatim, which for a pgx dial failure can carry
    host/port/user/database into the browser.
    forcing: none

45. ✅ **CLOSED — it was never work of its own, and its target closed first.** Rank 51 merged as
    `innovation-upstream/devrc#1515`, squash `cf77128d`, and was evicted to this file in the
    2026-09-14 sweep — so from that moment 45 was a live queue entry whose only content was a
    pointer into the closed-ranks ref. 🔴 **A "see rank N" entry outlives its target silently**:
    nothing links the two, so evicting N leaves the pointer reading as open work. An entry whose
    whole body is a redirect should be evicted WITH the rank it redirects to.
    ORIGINAL: **SUPERSEDED BY RANK 51 — same PR (#1515), do not claim both.** Left in place because
    ranks are stable; work it at 51.
    forcing: none


<!-- Second sweep, 2026-09-14. The four below were evicted together with 42 and 45; each carried
     its closure IN ITS BODY (or, for 9, was never a work item), which is exactly why the first
     sweep's heading-line scan walked past them. The ORIGINAL text follows each closure note
     verbatim, INDENTED so it cannot open a second numbered entry for the same rank. -->

9. ✅ **NOT A WORK ITEM, and it never was** — it held a rank only so the numbering stayed sparse.
   Evicted 2026-09-14: a placeholder in a queue is indistinguishable from work until you read it,
   which is the cost this file exists to stop paying.
   ORIGINAL, verbatim:
    9. **There is no rank 9** — a previous revision listed one and it was never a work item (the
       operator confirmed 2026-08-27 that MEMORY.md is not used here).
       forcing: none

17. ✅ **CLOSED — its closing condition SHIPPED, and the entry never said so.**
    `ZacxDev/homelab-infra#685` — *"make the health wait evidence-driven — fast-fail on a dead
    server, patient with a slow one"* — **MERGED 2026-09-05T00:36:56Z, squash `51eb4e0ea8005`**,
    the day after the entry was written. The body below still reads *"is this item's closing
    condition, in flight"*, present tense, nine days stale.
    🔴 **THIS IS THE ENTRY THAT BROKE THE RULE IN THE SAME COMMIT THAT WROTE IT.** The first
    version of this sweep KEPT 17 in the queue under a paragraph restating *"another session owns
    #685"* — copied from the body, never re-measured — two lines below its own new sentence *"an
    item's own status line is not evidence of its status."* An audit round caught it. **A sweep
    that re-measures what it EVICTS and quotes what it KEEPS has not done the thing it claims.**
    ⚠ It was ALREADY marked `CLOSED AS NOT-OURS 2026-09-04` in its body and still survived the
    first sweep — one of the three items that dated the heading-line diagnosis.
    ORIGINAL, verbatim:
    17. **The e2e/ux-audit harnesses' 15s health-check budget produces MISATTRIBUTED CI reds.** Repo:
        `homelab-talos`, `containers/clawgate/e2e/tests/helpers/server.ts:372` (the throw) and whatever
        sets the 15000ms budget it reports. Both `clawgate-e2e` and `clawgate-ux-audit` stand up an
        ephemeral clawgate + Postgres and fail with `clawgate health check did not pass on port <N>
        within 15000ms` when the box is loaded. **Measured 2026-09-02 on #637:** the same check
        alternated Fail/Success across three commits that changed only string literals in one Go test
        file, the failing TEST moved between runs, and `clawgate-e2e` also failed on `b2fecf49` — a
        commit already merged to trunk and not from that PR. A passing run cleared the budget by
        **854ms against 15000ms**, so this is a startup race, not a margin being approached.
        Closing condition: a red on either check can be attributed to a diff without a re-run — e.g.
        the budget scales with load, or the harness retries, or the failure names the contended
        resource. Until then, re-run the PipelineRun from its own spec (recipe in "How to verify").
        🔴 **CLOSED AS NOT-OURS 2026-09-04 — ANOTHER SESSION IS ALREADY BUILDING THIS. DO NOT START IT.**
        `ZacxDev/homelab-infra#685` — *"make the health wait evidence-driven — fast-fail on a dead
        server, patient with a slow one"* — is this item's closing condition, in flight. Siblings on the
        same platform problem: **#684** (log lock waits on the clawgate-ci Postgres sidecar — rank 18's
        instrumentation) and **#687** (stop blaming the commit for platform timeouts).
        🔴 **NOTHING CLAIMED IT, so `claim-work` could not have seen it — only the unconditional
        `gh pr list` sweep did.** That is the class the sweep exists for and the lock structurally
        cannot cover.
        ⚠ **AND THE ITEM AS WRITTEN WAS TOO NARROW TO HAVE WORKED.** It prescribes a budget fix in
        clawgate's two e2e harnesses, but the degradation is platform-wide — measured 2026-09-04 from
        PipelineRun history: `clawgate-ci` 3/18, `gitops-validate` 6/22, `clawgate-ux-audit` 5/18,
        `devrc-ci` 9/20, `naida-ux-audit` 9/20, `clawgate-e2e` 11/18, and only `remix-ux-audit` (15/16)
        healthy. A timeout constant in `containers/clawgate/e2e/tests/helpers/server.ts` cannot reach
        `gitops-validate` or `naida-ux-audit`; they do not run that code. The real remedies are rank
        18's node diagnosis and the dedicated CI hardware another session shipped the same day
        (Hetzner ccx33 `tekton-ci-1`, 8c/32G) — **already observed taking work**: `clawgate-ci-g4gcm`
        scheduled across `talos-xr6-r7p` and `tekton-ci-1`.
        ⚠ **Its own signature is still live and still worth recognising** — measured on `#690`'s head
        the same day: `tasks-mobile.spec.ts:531` failed all three attempts with
        `clawgate health check did not pass on port 39531/40037/41167 within 15000ms`, i.e. in FIXTURE
        SETUP, before the test body ran, on a diff that only swapped two CSS width classes.
        forcing: gate — two of the four checks on every clawgate PR produce reds that are not about the
        change, which is the permanently-red-gate shape: it trains readers to click through.

25. ✅ **DONE — and it said so in its own body since 2026-09-05, through two sweeps.**
    `ZacxDev/homelab-infra#695`, squash `8f6aa6d3a`, content-verified on trunk; the host-side half
    `innovation-upstream/devrc#1310` merged; claim released, re-confirmed 2026-09-14 against
    `claim-work --list`. ⚠ Its note that this doc's **"Attention queue" section is STALE** is
    itself now stale: that section was CORRECTED 2026-09-04 and the row reads `✅ LIVE`. Nothing
    to fix there — recorded so a reader is not sent after it. (Quoting a status instead of
    re-measuring it is the habit this whole sweep exists to punish; this one was just cheap.)
    ORIGINAL, verbatim:
    25. **The transcript feeder — get Claude Code transcript content from both hosts into clawgate,
        read-only.** Repo: `ZacxDev/homelab-infra` (ingest) + `innovation-upstream/devrc` (the host-side
        push). ✅ **DONE 2026-09-05 — `ZacxDev/homelab-infra#695`, squash `8f6aa6d3a`**, content-verified
        on trunk. The devrc host-side half is `innovation-upstream/devrc#1310`. Claim released.
        🔴 **THIS DOC'S ATTENTION MODEL WAS STALE AND THE RECON REFUTED IT.** The "Attention queue"
        section above says `AskUserQuestion` is *"🔴 Silent today — `hook/clawgate-hook.sh:79`
        explicitly defers to the terminal without contacting the server"* and calls the hook change
        the primary use case. **That is false as of 2026-09-04.** `raise_attention_question` exists at
        `hook/clawgate-hook.sh:102` and fires on `AskUserQuestion` at `:201`; questions ARE reaching
        the queue, with their options. Measured live: **36 open entries — 2 `question`, 34 `idle`.**
        Do not re-derive the old model from that section; fix the section when you next touch it.
        **Why a transcript feeder at all:** the operator asked for a "pretty chat view" of session
        content. The right source is the **Claude Code JSONL transcript**, not `capture-pane`.
        Measured: transcripts sit at `~/.claude/projects/<slugified-cwd>/<session-uuid>.jsonl`, are
        newline-delimited JSON with `type` in {`assistant`,`user`,`attachment`,`system`,`mode`,
        `permission-mode`,`bridge-session`,`last-prompt`,`ai-title`} (one file: 133 records = 39
        assistant, 38 attachment, 17 user), and **the join key already exists** — every attention entry
        carries the Claude Code session id. `capture-pane` is an ANSI screen dump: lossy,
        scrollback-bounded, turn boundaries guessable only from formatting. **Operator chose transcript
        for BOTH surfaces**, with no capture-pane fallback.
        🔴 **A write route under a ledgered prefix reds the build** — see rank 29.
        🔴 **TRANSCRIPTS ARE CAPTURED TEXT AND THIS REPO IS PUBLIC.** No real message body, prompt,
        model output, media path or third-party hostname in a fixture, golden, debug dump or PR body.
        Fixtures must be SYNTHETIC and regenerated to the shape.
        Closing condition: transcript content for a named session on EACH host is retrievable from the
        pod, and the path is read-only — no host-side execution of any kind is reachable through it.
        forcing: none — no deadline; it is the foundation rank 26 needs.

26. ✅ **DONE — same PR as 25, same two-sweep survival.** Shipped in `#695`, squash `8f6aa6d3a`;
    claim released, re-confirmed 2026-09-14. 🔴 Its durable half is that `/session/{id}` is a
    CROSS-AGENT CONTRACT rather than an implementation detail, and that disjoint file ownership
    between two concurrent agents is NOT merge safety.
    ORIGINAL, verbatim:
    26. **The chat view — one transcript-driven component, mounted twice.** Repo:
        `ZacxDev/homelab-infra`, `containers/clawgate/internal/ui/`. ✅ **DONE 2026-09-05 — shipped in
        `#695`, squash `8f6aa6d3a`.** 🔴 The `/session/{claudeSessionId}` contract was verified against
        the REAL binary, not asserted: the href read out of the card's own `AttentionSessionPath`
        constant returned **200** with the session id in the body, and a bogus path returned **404** as
        the negative control — so "not 404" is a measurement. Claim released. Originally dispatched
        2026-09-04, claim `tmux-webapp-26`. Renders a transcript as an app-native chat (user vs
        assistant turns), NOT a terminal dump. Two mounts: the tmux page, and a new standalone
        **`/session/{claudeSessionId}`** (shell) + **`/ui/session/{claudeSessionId}`** (partial).
        🔴 **`/session/{id}` IS A CROSS-AGENT CONTRACT, NOT AN IMPLEMENTATION DETAIL.** Rank 27's agent
        adds the attention card's "view session" link pointing at exactly that path, in a different
        worktree, concurrently. Renaming the route silently breaks a link nobody will test together —
        the isolation-seam shape. **Merge 26 before 27, or the link 404s.**
        ⚠ **File ownership was split to keep the two agents off each other:** 26 owns
        `internal/ui/tmux.go`, `internal/api/tmux.go` and the new chat/session files; 27+28 own
        `internal/ui/attention.go`, `internal/api/attention.go`, `internal/attention/` and `hook/`.
        🔴 **Disjoint files are NOT safety** — test-merge the two branches before merging the second.
        Closing condition: a Claude Code session renders as a readable chat at
        `/session/{id}` and from the tmux page, sourced from the transcript, verified on the live pod
        after deploy — not inferred from a green test.
        forcing: none

47. ✅ **CLOSED 2026-09-14 — the fix is UNCONFIRMED against the symptom, which is the closing
    condition as written.** The operator reports the symptom gone and names the workbench Brave
    `work` profile as where they saw it; nothing measurable distinguishes which change fixed it.
    🔴 **RETRACTED IN REVIEW — the first version of this entry claimed "the answer is NO, `#796`
    CANNOT have fixed it", and that was FALSE.** It rested on "a v1 zero proves no group was ever
    collapsed here, because nothing in the app ever deletes a v1 key." The immortality is real but
    it **begins at `#797`** — and `#796` merged **6 h 10 m earlier**, inside the window where v1
    keys were deleted by two ordinary paths. Measured at `#796`'s own merge commit `e77b79f5b`:
    `setCollapsed(k, false)` calls `s.removeItem(PREFIX + k)`, so **un-collapsing deletes the
    key**; and `prune()` (`:1372`, arms at `:1379`/`:1382`) scanned `PREFIX`/`ACKPREFIX`, which
    **were the v1 spellings then**, so v1 keys were garbage-collected whenever a project left the
    page. A v1 zero today is therefore **uninformative** about the v1 era.
    🔴 **THE ERROR WAS THE TIME AXIS, AND THE EVIDENCE WAS TWELVE LINES AWAY.** `prune()` was read
    on CURRENT trunk and its property asserted of the PAST — the exact thing `RULES.md` names
    ("current source is evidence about CURRENT behaviour, never what the code did BEFORE a fix").
    The `removeItem` that refutes it sits ~12 lines below the `var PREFIX` constants this entry
    already quotes from that same blob. **Reading the right commit is not enough if you read only
    the line you went looking for.**
    ⚠ **AND THE CONCLUSION WOULD NOT HAVE FOLLOWED EVEN IF THE PREMISE HELD.** `#796`'s diff is
    server-side Go (`needsHuman`, used in `groupByProject` and `tmuxWindowCard`); it never touches
    the `tmuxGroupScript` JS and never reads `PREFIX`. It has **three** observable effects and only
    the force-open one needs a collapse key — the `WAITING ON YOU` group badge and the card's
    waiting-evidence banner render regardless. So "no collapse state" would rule out one of three
    effects, not the PR. Which reading "sessions appear missing" has — *cards absent* vs *could not
    find the window that needed me* — was never pinned down, and it decides this.
    🔴 **SO: `#796` IS NOT RULED OUT. It remains a candidate alongside `#797` and `#803`, and none
    of the three was measured.** Do not read this entry as eliminating any of them.
    🔴 **THE ITEM'S OWN INSTRUCTION NAMED A DEAD KEY PREFIX.** It says to read
    `cg.tmux.group.*`. That was the LIVE namespace when the item was written (2026-09-11T21:55Z)
    and was retired **1 h 44 m later** by `#797` (2026-09-11T23:39Z), which re-keyed the group
    identity from `<project>` to `<host>|<session>` under `cg.tmux.v2.group.`. Following the
    instruction literally today reads a namespace nothing writes, gets a structural zero, and
    reports "collapse is not active" having measured nothing. **A stale claim invalidated by a
    LATER change of the same arc, with nothing pointing at it** — the shape this doc keeps hitting.
    **MEASURED** on the profile the operator names as where they saw it (Brave `work`, workbench,
    origin `https://clawgate.zacx.dev`), with a positive control proving each predicate matches
    its real key shape (`v1=1 v2g=1 v2a=1` against synthetic keys) so a zero is a real zero:

        cg.tmux.group.*      (v1, dead)  ->  0
        cg.tmux.v2.group.*   (v2, LIVE)  ->  0
        whole origin: 2 keys — cg.session.view.<uuid>, htmx-history-cache

    🔴 **NEITHER ZERO PROVES WHAT THE FIRST VERSION OF THIS ENTRY CLAIMED.** The retraction above
    has the measurement; what survives is only this, and it is weak:

    | namespace | a zero proves | it does NOT prove |
    |---|---|---|
    | v1 | nothing is stored under the dead prefix today | **anything about the v1 era** — the key was deleted on un-collapse (`removeItem`, `e77b79f5b`) and pruned whenever the project left the page |
    | v2 | nothing is collapsed RIGHT NOW | that nothing ever was |

    The immortality property is real on CURRENT trunk — `prune()` (`:2100`) has two arms, both
    testing the v2 `PREFIX`/`ACKPREFIX` (`:2107`, `:2110`), so a v1 key matches neither, per the
    deliberate comment at `:1758`. **It simply does not extend backwards past `#797`**, and `#796`
    is on the far side of that line.
    ⚠ **HOLES, ENUMERATED RATHER THAN COUNTED — an earlier draft said "the one hole" and named the
    LEAST likely member of the family, which reads as an exhaustive residual-risk statement and is
    not one.** A v1 zero today is consistent with all of:
    1. never collapsed;
    2. collapsed, then un-collapsed — `removeItem`, no deliberate act required;
    3. collapsed, then the project left the page and v1-era `prune()` swept it;
    4. a manual `localStorage.clear()`;
    5. a different profile, a profile reset, or another origin — this read covers ONE origin in ONE
       profile;
    6. **the phone**, which the ORIGINAL text below names as *"the untested case"* and which this
       closure never measured. It is retired by the operator's statement that they saw the symptom
       on the workbench `work` profile — **an answer, not a measurement**, and it is recorded that
       way so a reader can weigh it.
    ⚠ **A near-miss worth recording:** the first read of the `var PREFIX` constant piped `git show`
    into `grep -nE` and returned **nothing**, which reads exactly like "the namespace did not exist
    there". `git grep` over the same commit found both lines. ⚠ It does **not** reproduce — a later
    audit ran the same shape successfully — so the cause was probably the pattern, not the pipe,
    and no mechanism is asserted here. The lesson stands either way and is already a standing rule
    (`RULES.md` → "Cross-check against a second tool that fails differently"); this is one worked
    instance of it, not a new rule.
    🔴 **WHAT DID FIX IT IS NOT ESTABLISHED, AND NO MECHANISM IS OFFERED.** The operator reports
    the symptom is gone. The page was rebuilt twice in the interval — `#797` (regroup to
    host -> session -> window) and `#803` (full rebuild) — so either is a candidate and neither was
    measured. **Do not supply a cause**; this arc has now produced three retracted explanations
    written under exactly this pressure.
    Closing condition as written ("the fix is unconfirmed against it") is MET — negatively, which
    is a real answer and not a failure to answer.
    ORIGINAL, verbatim:
    47. **Confirm the tmux collapse defect was the operator's actual symptom.** #796 fixed a real
       force-open bug, but the connected Brave profile has **zero** `cg.tmux.group.*` keys, so the
       collapse is not active there. Read that localStorage on the device where sessions appear
       missing (a phone is the untested case).
       forcing: user — the operator reported missing sessions; the fix is unconfirmed against it.
