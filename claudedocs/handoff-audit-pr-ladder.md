# Handoff: audit-pr-ladder — 2026-08-28

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Evaluate the `audit-pr` skill against how its round-ladder actually behaves in real
sessions, then fix what the measurement exposed. It exposed that the ladder's
findings-keyed stop rule does not terminate in the guard-hardening regime.

## State now
- ✅ **RANK 1 IS CLOSED — the fleet is converged and drift-check is rc 0.** Both hosts at
  `9a7c433865ef`, switched, consumer-checked (`managed artifacts resolve 0 dangling` /
  `CURRENT 0 stale` on each). `drift-check.sh` exits **0** with `PARITY-RC=0` on both — which is
  affirmative, not an absence: rc 10 (behind) is a parity-arm code, so a zero there is the check
  PASSING, not the check being skipped.
- ✅ **RANK 2 IS CLOSED — and NOT by this session.** `devrc-seq-absent-empty`, `devrc-auditfix`
  and `devrc-ho2` are all gone from `git worktree list`. Verified mechanically with a positive
  control (pattern matched a known-present worktree = 1, the three = 0) rather than by eye.
  🔴 **But the underlying condition is far worse than the item said: 137 worktrees are still
  registered**, most on merged branches, ~90 of them under `.claude/worktrees/agent-*`. The
  ranked item named three; retiring those three changed almost nothing.
- 🔴 **PREVIOUS DOC'S STATE LINES WERE STALE IN TWO WAYS — both found by measuring, not reading.**
  (a) The workbench was recorded as sitting on `feat/memory-detail-click`; it was back on `main`
  and **also 1 commit behind**, so rank 1 was a TWO-host ship, not one. (b) rc 17 was recorded as
  closed at the output on both hosts; it **had re-opened on the laptop**.
- ⚠ **`ship.sh` returned rc 19 on the first pass — the documented mid-run race, not a fault.**
  `origin/main` merged between the two hosts' fetches: workbench landed `57b010fb`, laptop
  `9a7c4338`. Every per-host check passed on both because each host really was at origin/main
  *as it saw it*. The second pass converged both, which is the remedy the tool prints itself.
- ✅ **rc 17 (laptop) CLOSED.** The laptop was building `clawgatectl` from a
  `homelab-talos/containers/clawgate` subtree 2 commits stale. Fixed with drift-check's own
  printed remedy: `git -C ~/workspace/homelab-talos pull --ff-only` on a **re-verified-clean**
  tree (`f7b07be3` → `22b250be`, 0 ahead, so ff-only could not conflict; `f7b07be3` is the
  rollback point), then a `home-manager switch` — a pull alone changes nothing nix manages.
  Laptop now serves `clawgatectl 0.8.19` from a real store path.
  🔴 **Scoped honestly: the drift was real at the CHECKOUT and its BINARY impact was nil.** The
  whole 17-commit pull touched exactly four files under `containers/clawgate` — two `.bats` and
  two `_test.go` — none of which reach a compiled binary. The laptop was never running wrong
  code. Derived from the pull's own diffstat, not from the commit titles.
  Source parity is now `compared=2 same=2 differing=0`; the workbench's `homelab-talos` reached
  the same `22b250be` during this window **by another session, not by this one**.
- 🔴 **HOST DIVERGENCE PERSISTS, UNCHANGED AND NOT MINE TO CLOSE.** `ship.sh` states it outright:
  `DIRTY AND IN THE ARTIFACT — nix reads 1 path(s)`, namely `nix/pkgs/default.nix`, so the
  workbench generation is `origin/main` **plus** an uncommitted `inxi` + `cpu-x` hunk that is
  another session's work. The laptop has neither package. **Two hosts, same sha, different code**
  — invisible to git parity. Re-shipping PRESERVED this state rather than creating it.
- **Three untracked files on the workbench**, none in a nix-read path (`hits=0` of 160 paths), so
  none is deployed: `output.txt`, `scripts/diagnose-nix-disk.sh`,
  `scripts/tests/test_opencode_rig_control.py`. 🔴 The last is an unsaved TEST with **no owner
  identified** — `claude/RULES.md` classes that as unsaved work one routine `checkout` from
  silent deletion.
- **Both preserved WIP dirs still exist and were not touched:**
  `~/workspace/.wip-preserve-discord-embed-2026-08-28/` and
  `~/workspace/.wip-preserve-memory-detail-2026-08-30/`.
- **Prior merges, carried forward (verified by CONTENT in an earlier session, never ancestry):**
  `#1109` → `8c61f2e6`, `#1111` → `f081167d`, `#1133` → `5324bf47`.
- **This doc's own update:** branch `docs/handoff-aplr-rank1-shipped`, worktree
  `~/workspace/devrc-ho-aplr2`. No `clawgate-task:` field is recorded — `clawgate_handoff.sh
  resolve` returned **rc 5 (nothing resolved) with its positive control passing**. That is not
  "no task": an unknown session id also answers 200 with an empty array.

## Closed investigations — both were diagnosed on 2026-08-28

### `discord-embed-ext` WIP: OWNER FOUND — an opencode session editing the base clone
- **Owner (measured, not inferred):** opencode session `ses_fbe5f77a2ffeaJr0G0S7i4lUKa`
  ("Find Discord media extension"), `directory=/home/zach/workspace/devrc` — **the base
  clone, with no worktree**. `~/.local/share/opencode/log/opencode.log` names the writes:
  `run=020e36c2 message="touching file" file=…/embed_enlarge.js` at `2026-08-29T02:07:44Z`,
  matching the file mtimes to the second. Its process is still alive (started 08-27 00:10)
  but idle since 21:08:33 — its last message is *"Deployed `v0.2.3` … Reload the extension
  in Brave."* 🔴 **It is blocked on Zach, not abandoned.**
- 🔴 **No Claude Code transcript contains an Edit/Write to that path** — searched every
  `~/.claude/projects/**/*.jsonl`, one hit and it was this session's own query. Looking only
  at Claude Code sessions would have concluded "nobody owns it". **Search BOTH runtimes.**
- **The WIP is unlanded and unique:** working-tree content matches no branch. `origin/main`
  is byte-identical to merged `#947` for all three paths, so this is newer than
  2026-08-27T23:16Z. `manifest.json` says `0.2.3`; `origin/main` says `0.1.0`.
- 🔴 **`ship.sh` baked it into the workbench generation.** `~/.local/share/discord-embed-ext`
  (what Brave loads) is `0.2.3` and carries `ATTR_CLEARED` ×5 — deployed 22:37 by this
  session's own ship run. The laptop got `0.1.0`. **Same sha, different code — confirmed
  with version numbers, not inferred.**
- **PRESERVED, not touched:** `~/workspace/.wip-preserve-discord-embed-2026-08-28/` holds
  the three files plus `discord-embed-ext.patch`; `git apply --check --reverse` confirms the
  patch matches the tree exactly. The tree itself was left dirty and unmodified — 🔴 do NOT
  `checkout --` it.
- 🔴 **A defect the owner is not looking for — REPRODUCED with both controls.** `observe()`
  calls `observer.disconnect()` unconditionally, then reconnects only `if (found > 0)`
  (`embed_enlarge.js:150–161`). Any debounced batch with no media — a typing indicator,
  scroll, presence — leaves the observer **permanently disconnected**, so no later
  attachment is ever enlarged. Measured: WIP `v0.2.3` → `connected=false`,
  `batch_seen=false`, `enlarged=false`; control `origin/main v0.1.0` → `connected=true`,
  `batch_seen=true`, `enlarged=true`. Introduced by `v0.2.2`'s "observer disconnects during
  style changes". **The session has spent 0.2.2→0.2.3 iterating on CSS selectors while its
  own observer teardown is what breaks it.**
- 🔴 **The shipped harness structurally cannot catch this.** No test calls `observe` at all;
  `FakeMutationObserver.observe/disconnect` are **no-ops** so connection state is
  unobservable, and `FakeElement` sets **no `nodeType`**, so the callback's
  `node.nodeType === 1` gate is false and `markMediaElements` is never reached. A first repro
  run looked like it reproduced and was **vacuous in both arms** (`enlarged=false`
  everywhere) until `nodeType: 1` was set on the fixtures — the positive control is what
  caught it.

### The `localverify` remote: written ONCE on 2026-08-23, and "written repeatedly" was WRONG
- **Writer (exact):** the `civit-datapacket-talos` session
  `5e58d9a0-0a99-4c9c-bf65-e40b403e4d55`, at `2026-08-23T06:06:49Z`, verifying the githooks
  pre-push gate:
  `git -C /tmp/wt-hookcheck remote add localverify /tmp/verify-remote.git`.
  🔴 **`/tmp/wt-hookcheck` was a WORKTREE OF devrc** — its own cleanup ran
  `git -C ~/workspace/devrc worktree remove --force /tmp/wt-hookcheck`. Remotes live in the
  **common** config, so the entry landed in the shared clone. That cleanup (06:07:05) removed
  the worktree, the branch and `/tmp/verify-remote.git` — but **not the remote entry**, which
  is why it pointed at a directory that does not exist.
- 🔴 **The "it is written repeatedly" reading was an empty-result error, now refuted by
  measurement.** The evidence for it was `.git/config` mtime moving; that config holds **442
  `[branch "…"]` sections**, and every `checkout -b`/`push -u` in any of ~40 agent worktrees
  appends one. A watcher run during removal caught the rival mechanism in the act: at
  `22:41:41` the remote went 2 lines → 0 (my removal), then at `22:43:49` config changed
  again with `localverify` still **0**, the diff being
  `[branch "docs/handoff-tmux-webapp-rank3-done"]` from another session. **mtime cannot
  distinguish the two writers — the content diff can.**
- **FIXED:** `git -C $DEVRC remote remove localverify`, rc 0. Removal also deleted the one
  leftover ref `refs/remotes/localverify/hookverify` (`dcda00b5`, a throwaway README append),
  recorded here so the step stays reversible. Verified gone; not re-added since.
- 🔴 **Bonus — this closes an open question in `CLAUDE.md`.** The same session set devrc's
  repo-local `core.hooksPath` at `2026-08-21T22:16:14Z` and unset it itself at
  `2026-08-23T21:32:51Z`. That is the `githooks/` sighting CLAUDE.md records as coming "from
  something else". It does **not** explain the 08-20 `.git/hooks` sightings in devrc and
  homelab-talos — those remain unattributed, and the value is still volatile, so keep
  measuring it rather than trusting prose.
- 🔴 **The generalisable hazard: a worktree does NOT isolate the REMOTE SET, or any
  `git config` write.** `git remote add`, `git config --local`, `core.hooksPath` all land in
  the common config and are seen by every worktree and the base clone. Belongs with the
  other "surfaces a worktree does not hand you" in `claude/RULES.md`.

## Next steps (ranked)
🔴 **Numbering is deliberately STABLE.** The rank is half a `claim-work` slug's identity, so
renumbering silently re-points every live claim. Closed items are retained as DONE markers.

1. **DONE (2026-08-31) — ship the laptop.** Both hosts converged; `drift-check.sh` rc 0,
   `PARITY-RC=0` on both. Do not re-claim.
   forcing: none
2. **DONE (found already closed 2026-08-31) — the three leftover worktrees.** ⚠ The successor
   condition is real and unowned: **137 registered worktrees**, most on merged branches. Not
   filed — no named owner and no checkable closing condition, so a ticket would read as covered
   while nothing could close it.
   forcing: none
3. **DONE (2026-08-31) — `#1133`'s never-run round 3.** Run blind over `ea36a489..9ff5c9a9`. It
   returned 4 🟡 + 1 🟢. Exactly one was live in `main` — the escape hatch's summary obligation,
   deleted by a reword — fixed and pinned in **`#1157`**, which then ran its own three-round
   ladder and was **stopped on the stated criterion, not on a clean round** (rationale recorded
   on the PR, per the clause `#1157` restores). The other four findings were not actionable
   against `main`: `#1117` had already removed the code they describe. See rank 4.
   forcing: none
4. **The `nix log` fallback port-back that `#1117` deferred — with acceptance criteria already
   measured.** (repo: `devrc`; file `scripts/audit-dispatch.py`.) `1f0fe4c1` (#1117) rewrote
   `render_toolchain` 2h36m after `#1133` merged, carrying forward only `--no-link -L` and
   deferring the rest to "its own PR and its own round". Verified: `git show
   origin/main:scripts/audit-dispatch.py | grep -c path-info` → **0**. 🔴 **These four are
   measured, not speculative — a port-back that reintroduces the old block ships all of them:**
   - **Do not carry the stderr parenthetical** claiming `nix path-info`'s stderr is discarded by
     the `>` two lines down. Measured: that `>` belongs to a different command and redirects
     stdout; the flake error lands on the terminal. It also contradicts the same block's correct
     "`-L` WRITES TO STDERR" rule 28 lines above.
   - **Do not carry the fixed `/tmp/tier.log`.** Two parallel audit agents truncate each other's
     file and each greps the other's tier — with all three guards passing. Use `mktemp`.
   - **Add a structural guard for the guard set.** Measured: deleting the `[ -n "$DRV" ]` emitter
     left the suite byte-identical (301 passed); a `.pytests`→`.PYTESTS` positive control on the
     same emitter went red, so the harness reaches those lines and simply cannot see these.
   - **"THEY CLOSE THREE DIFFERENT FALSE GREENS" is wider than the bullets deliver** — the list
     supplies two.
   **Closing condition:** a PR restoring that block lands with all four addressed, or a named
   reader records in writing that the fallback is not coming back.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **The ladder never returned a clean round in twelve.** The stop rule assumes
  convergence; in the guard-hardening regime each fix writes guards that become the next
  round's audit surface. Stopped on a stated criterion instead: no 🔴, no blast radius
  beyond "the brief contains a false sentence", and the recurring shape swept at EVERY
  consumer of the touched predicate. Full evidence in
  `claude/skills/audit-pr/reference/round-ladder-evidence.md` (landed by `#993`).
- 🔴 **The `#900` attribution gate cannot rescue a ladder whose payload IS PROSE.** It
  stops after two zero-payload rounds; here "fixed a defect" and "reworded a warning" are
  frequently the same edit. Structural blind spot on any generator/docs/prompt PR.
- **Nine instances of one shape**, each a predicate read as a STRONGER fact than it
  carries. Twice it was *a default stated as a fact* (`baseRefName or "main"`, then
  `REPO_UNKNOWN`'s own sentinel text). Sweep every consumer of a predicate you touch.
- **`--deselect` with an ABSOLUTE path matches nothing and pytest is silent.** Positive
  control: absolute → 9789 collected (inert), relative → 9187/9789 (602 deselected).
- **`-q -q` suppresses pytest's `N passed` line entirely**, leaving only a piped exit
  status — which is `tail`'s, not pytest's.
- **`$(...)` strips trailing newlines**, so a newline-rejection probe passes vacuously
  through command substitution; pass the value through argv instead.
- **`gh` has no `baseRefteName`-style `baseRepository` field** — use `url` +
  `isCrossRepository`.
- **Editing a file in the shared clone while it sits on `main`** violates
  feature-branches-only. Recovery used here: save the edit aside, `git checkout --` to
  restore byte-identical to HEAD, redo in a branch worktree.
- **A rescued commit gets a PR for a reason** — `#979` was already live on the workbench
  and its required check caught a real defect (`ask` gone ambiguous).
- **Five flakes in `test_subsystem_store_api.py` in one day**, all on unrelated PRs, and
  its assertion message described the OPPOSITE of the failure (`len(answers) == 0`,
  `raw == b''`, under the text "a second response followed the 200"). Four cycles were lost
  to that message before a pod was read in time. 🔴 **NOT CLOSED — this doc previously
  said `#996` closed it, and 2026-08-29 falsifies that.** `#996` fixed audit ordering and
  serialised the sink; it never touched the CLIENT bound, which is where the flake
  actually lives. The same file went red again on `TestTheActorComesFromTheTOKEN`, on
  MULTIPLE unrelated PRs at once, out of `socket.py` with `TimeoutError` — a 15 s
  localhost read losing the scheduler while 12 Tekton pipelineruns shared the node and
  this suite ran 637 s under xdist. **6 of 10 devrc runs in one window failed this way.**
  Fix in `#1015` (one `HANG_TIMEOUT`, 15 s → 60 s, hang-detector proven still to fire at
  the new bound). 🔴 That is the SYMPTOM: the cause is a 10-minute parallel suite
  competing with a saturated cluster, which is Tekton capacity and not this repo's file.
- 🔴 **A fix landing is not the same as a family closing** — and the tell is that the
  claim was written from the fix's *description* rather than from what it touched. Before
  writing "closed", name the mechanism and check the fix actually reaches it.

- 🔴 **CARRIED FORWARD from the ranked list, which this update replaces — corrections to
  `#1023`'s own commit messages, kept because that history is MERGED and will not be
  rewritten.** (a) the espanso trade-off is **9** lost multi-word queries, not 8 — `ask agent`
  also stops reaching `:dacq`, via the label word `subagent`, and `--diff-config`'s probe
  universe forms two-token pairs WITHIN ONE SNIPPET so that pair never exists to be tested;
  the claim that matters — none of the 9 has ever been typed — still holds. (b) **"56
  recovered fires" measures ATTRIBUTION, not intent**: those fires were unattributable by
  construction, so the share that *meant* `:dacq` is unmeasurable, and `ask` no longer lists
  `:dacq` at all (2 picker rows → 1) while `:dacq` keeps all 8 of its unique routes. A
  legitimate, tool-sanctioned remedy — but not pure gain.
- 🔴 **THREE miscounted self-reports in one ladder, the last inside the guard written to
  stop them.** "all 7 sites" (6), "NINE settimeout sites" (8), "8 lost espanso queries" (9).
  Each was a number I produced and then trusted instead of re-deriving. The last is the
  sharpest: it came from counting `grep -n 'settimeout('` OUTPUT LINES, one of which was the
  docstring *mentioning* the name I was counting. **A grep counts MENTIONS; an AST walk
  counts CALLS.** Re-derive every count from the tree at the moment you quote it.
- 🔴 **Several of this thread's wrong claims were true of the INSTRUMENT, not the FILE.** A
  literal-string grep reported as a property of the tree; a `-k` filter that deselected the
  failing test and returned a confident "5 passed"; `xargs -0 command grep` returning a
  silent zero because `command` is a shell BUILTIN with no executable to run. Two of those
  stacked into a confident *"unexplained — same tree, opposite results"* about the opencode
  failure, which in fact reproduced locally in one run against the right file. **Before
  quoting a zero, make the instrument produce a non-zero on a case you know is there.**
- 🔴 **A DETECTOR-DRIVEN FIX INHERITS THE DETECTOR'S BLIND SPOT.** The pin re-key was
  line-targeted at what the ledger flagged, and the ledger only sees version literals — so a
  stale *rev* on a line with no version survived, untouched and unreported (`#1035`). Ask
  what the detector CANNOT see before treating its output as the work list.
- 🔴 **Neither PR could go green alone — a DEADLOCK, not a preference.** `#1015`/`#1021`/
  `#1022` each fixed one of three independent reds, and the gate runs the whole suite, so
  each red-tested the others' bug. Combining them into `#1023` was the only path to a green
  run. When several PRs each fix part of a repo-wide red, expect this and plan for one branch.
- **A ledger SNIPPET or COMMENT containing a version literal is itself a claim in the pin
  surface** — the entry's own source line becomes an old-version line and matches twice.
  Every entry is version-free for this reason. Tripped twice in one session, the second time
  in the comment written to explain it.
- **`settimeout(None)` survived the drain guard's first draft, fully green.** `ast.unparse`
  renders it `"None"`, which is not `.isdigit()`, so a size-based arm cannot see the one
  value that means *block forever*. Guards over rendered source need an explicit arm per
  non-numeric hazard.
- 🔴 **The store-api timeout fix is a SYMPTOM fix and the label was load-bearing.** It was
  shipped saying so, and `#1009` then failed at the new 60 s bound. Do not read `#1023` as
  having fixed the flake.

- 🔴 **A COMMENT IS A CLAIM, and this session found one contradicted by the line directly under
  it.** `nix/home.nix` said `2026-08-29: "ask" REMOVED from :dacq` immediately above a `:dacq`
  whose `search_terms` spelled `"ask"`. Zach had deliberately re-added it (`fc024d59`, direct to
  `main`), which turned `main` red repo-wide: `_attribute` returned `None` for `ask` AND
  `clarify` (the latter via the new `"clarifying"`).
- 🔴 **The FIX WAS THE MECHANISM, NOT THE CONFIG.** Deleting the terms from `:dacq` had already
  been the response twice and was reverted by hand both times. `search_terms` serves two
  consumers that want opposite things — the PICKER wants recall (ambiguity there means *two
  rows*, which `_PICKER_ROWS` exists to protect) and `_attribute` wants precision. So the config
  keeps both spellings and `_AMBIGUOUS_TERM_OWNER` declares the owner, consulted ONLY on the
  already-ambiguous branch, after `_names_trigger`, and ONLY over snippets the term matches
  (`owner in matched`). An ambiguous term with no entry still returns `None`.
- **MEASURED, and the answer was DON'T: no further owner entries are justified.** Crossed all 23
  ambiguous terms against the real keylog stream (661 espanso rows; positive control `ask` = 118
  fires, 24 of 27 typed terms resolve). Only four typed terms are still unattributed —
  `ssh la` (5), `ssh` (3), `gpu` (3), `clar` (1) — and the first three are unattributed
  *because the query genuinely does not identify a snippet*; `None` is the honest answer and an
  owner entry would fabricate attribution. 🔴 **`clarifying` has been typed ZERO times**, so the
  one-line entry that "obviously" belonged was pure speculation — same verdict, reached the same
  way, as the earlier `date` retirement.
- 🔴 **BLIND SPOT in the mechanism this session shipped: the owner lookup is an EXACT-STRING
  match while `_term_matches` is a SUBSTRING test, so PREFIXES of an owned term are not owned.**
  `clar` (1 fire) used to resolve and now does not. **ACCEPTED, not patched** — per-prefix
  entries cannot enumerate `cla`/`clari`/`clarif`, and a prefix-aware lookup can re-point terms
  the exact form never touched. Recorded in the code comment.
- 🔴 **A BUCKETING ERROR is why an order-safety audit had already walked past the flaky test.**
  `_wait_events`' docstring audits its n>=2 sites and concludes "All 8 remaining real waits are
  order-safe". The flaky call was `_wait_events(spool_dir, len(ORIGIN_TOKENS))` — an n=2 wait
  spelled with a NON-LITERAL `n`, which that counting (`39 n=1 + 5 until= + 9 n>=2`) folds into
  **n=1**, where no ordering argument is required. The ratchet's classifier had it in a `n
  dynamic` sub-bucket its own comment called "informational" *because both methods agreed on the
  total of 48*. **They agreed on the number and disagreed on the BUCKET, and the bucket was the
  part that mattered.**
- ⚠ **A correction, recorded because the first draft shipped it:** I wrote that the flaky site
  "was a NINTH n>=2 wait". It never was — it was never counted among the nine. Re-derived from
  `classify_wait_calls()`, never by hand.
- **Running SUBSETS is what let a ratchet failure through.** Browser-bridge + keylog passed
  locally; `test_positional_spool_reader_ratchet` lives in `scripts/tests` and failed in the
  sandbox tier with `POSITIONAL SPOOL READERS SHRANK: 47 (pinned 48, -1)`. The gate that gates
  is the sandbox tier — run it, not a subset.
- 🔴 **`grep -c` answered the OPPOSITE of the truth when verifying the merge.** Checking the
  ledger entry was removed returned `1` — which was the test NAME appearing in the pin COMMENT,
  not a ledger row. The ledger-format count is `0`. Grep counts mentions.
- **The devrc merge gate was WEDGED for the whole repo for ~35 min, and it was not a bad diff.**
  `homelab-infra` `6bec075e` replaced the gate's RWO PVC with a per-node hostPath cache;
  `tekton-ci` carries no PSA label so it inherits the cluster default `baseline`, which forbids
  hostPath — every gate pod failed ADMISSION in ~17s with `COULD NOT RUN: <leg>`. Diagnosed here,
  **fixed by another session** (`homelab-infra` `686d6ff0`, namespace `pod-security…/enforce:
  privileged`). 🔴 `error` ≠ `failure`: a check posted as `error` with `COULD NOT RUN` is a broken
  gate — do not debug your diff against it.
- **Confirmed working in the wild:** a run that burns its whole 60m budget in `Pending`
  (`devrc-ci-gwjm9`, `ExceededNodeResources`, never scheduled) still ran its `finally` report and
  posted `COULD NOT RUN` rather than leaving the check `pending` forever — homelab-infra `#386`'s
  task-level-timeout fix behaving as designed.
- **Branch protection moved twice in one night.** `required_status_checks.contexts` was `null`
  (the documented escape hatch) around 21:44Z and back to both Tekton contexts with
  `enforce_admins: true` by 22:39Z. Never carry its state in prose — re-measure with
  `gh api /repos/innovation-upstream/devrc/branches/main/protection`.

- 🔴 **A GUARD'S OWN DOCSTRING CAN BE THE REASON A HAZARD SURVIVES AN AUDIT OF IT.** `_wait_ops`
  claimed `where=` "keeps the order" for the one site in the file that unpacks a pair. An
  order-safety reader who reached that sentence had their question answered — wrongly — and
  stopped. The hazard was not hidden; it was **vouched for**. When a helper's docstring asserts a
  property, ask what the code must do to provide it: a per-row predicate is structurally
  incapable of ordering two rows that both satisfy it.
- 🔴 **A RATCHET COUNTS THE HAZARD IT WAS BUILT FOR, AND READS AS COVERAGE FOR THE FAMILY.**
  `test_positional_spool_reader_ratchet.py` ratchets the FOREIGN-row hazard (position vs
  discrimination) and has **no view at all** of the OWN-rows hazard. It was green throughout,
  before and after. That blind spot is now written into its pin comment, because the module's
  framing ("positional spool reads") reads much wider than what it measures.
- **Three stale counts found in the files this touched, each reading as precise:**
  `_wait_events`' docstring said `53 total = 39 n=1 + 5 until= + 9 n>=2 (+ 7 op-selected)` —
  re-derived by AST as `52 = 38 + 5 + 9`, **11** op-selected, wrong in three places at once,
  left behind by `#1074`; and the ratchet's ledger comment said "the same 48 sites" while its own
  pin was 47. Both corrected. The instrument that produces all of them is one call to
  `classify_wait_calls()` — the same rule the handoff already carried, hit again.
- **Bucket effect of an ordering fix, stated so the next one is not misread:** sequencing a pair
  **adds** a `_wait_ops where=` call (3 → 4, total 62 → 63) and **converts nothing** — the
  ratcheted number and the ledger are untouched. A pin that does not move is the expected shape
  here, not evidence the change did nothing.
- **The `NIXBUILD_RC=$?` after a `| tail` was written again this session** — the documented trap,
  reproduced by habit in the very command whose verdict mattered. It is `tail`'s status. The
  defence that worked was not remembering the rule; it was never quoting the number.
- **`/handoff` step 1 returned rc 5 (nothing resolved) with its positive control passing**, so no
  `clawgate-task:` field is recorded here. That is not "no task" — an unknown session id also
  answers 200 with an empty array.

- 🔴 **`| tail` ON A `nix build` DOES NOT JUST EAT THE EXIT CODE — IT EATS THE WHOLE VERDICT.**
  The documented trap is usually stated as "the piped `$?` is `tail`'s". Measured here, the
  damage was larger: `nix build … 2>&1 | tail -40` left **4 lines** of a **1,451-line** build log
  — `this derivation will be built`, `building '…drv'`, and the bogus `NIXBUILD_RC=0`. Every
  `RESULT:` line, the `PASS 48 FAIL 0` table and the whole per-target breakdown were gone. The
  output was not merely missing a status; it was **indistinguishable from a failed run's**.
  🔴 **The instrument that answers is `nix log <drv>`** — the derivation keeps its own full log,
  so a swallowed console capture is recoverable: take the `.drv` path from the surviving
  `building '…'` line (or `nix path-info --derivation`), write `nix log` to a FILE, and grep it
  for `RESULT:` and `panic: test timed out`. Do not re-run the build to get its output back.
- 🔴 **A CONTENDED-BUILD WARNING IS DIRECTIONAL, AND READING IT AS SYMMETRIC WOULD HAVE COST A
  RE-RUN.** CLAUDE.md says a combined/contended `nix build` produces false FAILURES; a green is
  trustworthy because contention makes a run fail loudly, not pass falsely. A sibling session was
  building `pytests` concurrently throughout this one. The green stood, unre-run. **Ask which
  direction a reliability caveat points before paying for it.**

- 🔴 **RETRACTED, AND THE RETRACTION IS THE LESSON: `| tail` WAS NOT WHY THE `nix build` VERDICT
  WAS UNREADABLE.** An earlier revision of this doc stated, as a measurement, that
  `nix build … 2>&1 | tail -40` discarded 1,447 of 1,451 log lines. **That diagnosis is false and
  its own evidence refutes it:** the surviving lines were the FIRST lines of the stream
  (`this derivation will be built`, `building '…drv'`), and `tail -40` keeps the LAST forty — so
  nothing was discarded. **The real cause: `nix build` does not print the build log of a build
  that SUCCEEDS without `-L`/`--print-build-logs`.** Paired control, measured, on a builder
  printing 201 lines plus a `RESULT:` line: **without `-L` → 3 console lines, 0 builder lines**;
  **with `-L` → `tail -40` keeps 40, including the `RESULT:` line as the last one.** So `tail -40`
  would have *preserved* the verdict had the log been streaming. 🔴 **The wrong fix this would
  have taught — "drop the pipe" — leaves you with exactly the same silence.** Use `-L`, or
  `nix log <drv>` after the fact, which also works when the derivation was already built and the
  console prints nothing.
  🔴 **"SUCCEEDS" IS LOAD-BEARING AND WAS ADDED BY A LATER AUDIT — the first version of this
  bullet said "does not print build logs at all", which is the same over-wide shape it was
  written to retract.** MEASURED on a FAILING build, no `-L`, non-tty: rc 1, **35 stderr lines,
  24 of them builder lines, including the `RESULT:` line**, then `For full logs, run: nix log …`.
  nix prints the tail of a failed builder's log inline, bounded by **`log-lines` (25 here,
  `nix config show | grep ^log-lines`)**. That is the case you are most often staring at, so a
  reader carrying "no log without `-L`, ever" mis-reads the one output that matters. ⚠ The
  "3 console lines" figure is also **non-tty specific** — on a pty the same successful build
  prints 0 — and assumes the derivation is not already cached (next bullet).
  🔴 **Generalise: a plausible mechanism you already know about is the most dangerous
  explanation, because it stops the search.** The documented `| tail` trap was real, adjacent,
  and not what happened; I reached for it and labelled the result "measured".
- 🔴 **AN ALREADY-BUILT DERIVATION PRINTS NOTHING — SILENCE IS NOT A PASS.** `nix build --no-link`
  on a cached output emits zero lines, with or without `-L`. Any verify block that says "run this
  and read the verdict" is unrunnable the second time. `nix log <drv>` is the instrument that
  survives caching.
- 🔴 **A HELPER'S DOCSTRING CAN BE THE REASON A HAZARD SURVIVES AN AUDIT OF IT.** **`_wait_events`'**
  docstring (`test_server.py:175` pre-`#1109`) said the pair site's `where=` "keeps the order".
  An order-safety reader who reached that sentence had their question answered — wrongly — and
  stopped. ⚠ **This doc first attributed that sentence to `_wait_ops`' docstring. It is
  `_wait_events`'** — re-derived by AST over `origin/main`. In a lesson about *which* text
  vouched for the hazard, naming the wrong helper destroys the lesson.
- 🔴 **A RATCHET COUNTS THE HAZARD IT WAS BUILT FOR AND READS AS COVERAGE FOR THE FAMILY.**
  `test_positional_spool_reader_ratchet.py` ratchets the FOREIGN-row hazard and has no view of
  the OWN-rows one. Green before, during and after — including while the guard test carried a
  live instance. Now stated in its own pin comment.
- **FOUR stale counts were found in the files this touched** (an earlier revision said "three"
  and then listed four): `_wait_events`' docstring said `53 total`, `39 n=1` and `7 op-selected`
  — actually 52 / 38 / **12** — and the ratchet's ledger comment said "the same 48 sites" while
  its pin was 47. One call to `classify_wait_calls()` produces all of them.
  🔴 **AND THE CORRECTION WENT STALE ONE COMMIT LATER — THIRD INSTANCE, INSIDE THE BULLET ABOUT
  STALE COUNTS.** This said `11 op-selected`, which was true at `2579e2f3` and was invalidated by
  **my own round-1 audit-fix commit** (`e4777c58`), which added a third `_wait_ops` call while
  sequencing the guard test: `_wait_ops where=` 3→5, op-selected 11→**12**. I re-derived after
  writing the fix and did not re-derive after *committing* it. **Nothing pins this number** — no
  assertion references `op-selected`, so a full green suite says nothing about it, which is why
  it drifted twice. The tell is structural: a count quoted in prose, in a file whose tests do not
  read it.
- ⚠ **An unverifiable corroborating hash is worse than none.** An earlier revision cited
  `sha256 1b42b227…` as proof the control mutation was reverted. It names an intermediate
  working-tree state that reaches no commit, so no reader can reproduce it — while reading as
  precise. The claim it supported is independently true (worktree file == commit == built
  source), and that is what should have been cited.
- ⚠ **A doc can contradict itself across sections within one commit.** An earlier revision said
  "RANK 1's CLOSING CONDITION IS MET" in *State now* while the ranked list had already redefined
  rank 1 as "merge both PRs" (unmerged) and an investigation block still said the tier verdict
  was open. When a ranked item is redefined, re-read the status section against the NEW item.

- 🔴 **AN APPEND-ONLY SECTION CANNOT BE CORRECTED BY APPENDING A CORRECTION.** `handoff_doc.py`
  merges `Open investigations` / `Findings` / `Gotchas` by APPENDING and keeps earlier text
  verbatim — which is the right default, and it means a delta claiming to "remove" a sentence in
  one of those sections removes nothing. Measured: round 1's claim that an unverifiable `sha256`
  citation had been removed was FALSE in exactly this way — the retraction landed under Gotchas
  while the original line kept making the claim ~140 lines above it. **To correct a line in an
  append-only section you must EDIT THE FILE, and say in the commit that you did and why.** Read
  the tool's `buckets:` line before believing a removal.
- 🔴 **A NUMBER NOTHING ASSERTS ON WILL DRIFT, AND A GREEN SUITE WILL NEVER SAY SO.** The
  `op-selected` count in `_wait_events`' docstring has now been wrong twice — first left behind
  by `#1074`, then re-staled one commit later by my own audit-fix, which added a `_wait_ops` call
  while sequencing the guard test. `git grep op-selected` finds only prose; no test reads it. The
  structural tell is general: **a count quoted in prose in a file whose tests never read it is
  unpinned by construction.** Either pin it or stop quoting it — re-deriving it by hand each time
  is what has already failed twice.
- 🔴 **RE-DERIVE AFTER COMMITTING, NOT AFTER WRITING.** Both stalings share one mechanism: the
  number was correct when measured and the *fix itself* then changed the tree. The measurement
  and the commit are different moments, and only the second one is what a reader will check.

- 🔴 **FIVE AUDIT ROUNDS ACROSS THREE PRs, AND EVERY SINGLE FIX INTRODUCED A NEW DEFECT.** Not one
  round of mine was clean. The code changes were right first time; the SENTENCES about them were
  not. The defects narrowed each round (a false citation → a widened census verb → a missing shell
  guard → a false ledger entry), which is convergence, but the rate did not reach zero.
  **The generalisable claim: dense normative prose written at speed is where the defects are, and
  a blind adversarial audit is the only thing that caught any of them.**
- 🔴 **A DELTA LADDER CANNOT SEE A CLAIM ITS OWN EARLIER COMMIT STALED.** `(+ 11 op-selected)` was
  true at `#1109`'s first commit and falsified by its second; **three delta rounds walked past it,
  four lines from the paragraph all three were editing**, because every round's range excluded it.
  Found only from OUTSIDE, by the audit of a different PR quoting the same number. Nothing pinned
  it — no assertion reads `op-selected` — so a green suite was silent. Remedy now in the skill:
  once per ladder, range-free, re-derive every count the PR's files assert.
- 🔴 **THE ATTRIBUTION GATE IS INERT WHEN THE PAYLOAD IS PROSE.** For a docs/skill PR the `.md`
  IS the payload, so no round is ever zero-payload and the two-zero-rounds gate cannot fire —
  while the ladder does exactly what the gate exists to catch. `#1111` was closed on the STATED
  criterion instead. Both findings landed in `claude/skills/audit-pr/` (`#1133`).
- 🔴 **THE AUDIT BRIEF INSTRUCTED AN IMPOSSIBLE READ, AND HAD FOR ITS WHOLE LIFE.**
  `audit-dispatch.py` emitted `nix build …#checks…` with **no `-L`** under "read each runner's own
  `RESULT:` line". `nix build` prints no build log for a build that SUCCEEDS without `-L`.
  Measured: without `-L` → 3 console lines, 0 builder lines; with `-L` → the log streams.
  ⚠ A FAILING build DOES print its tail inline (bounded by `log-lines`, 25), which is why the
  omission survived — only the green case was silent. Three of four auditors this session declined
  to run the sandbox tier; that is CONSISTENT with the defect and does not prove it caused them.
- 🔴 **`-L` WRITES TO STDERR — MY OWN FIX'S EXAMPLE CAPTURED NOTHING.** I wrote that
  `-L … | tail -40` keeps the `RESULT:` line. Measured: **0 lines, 0 hits** without `2>&1`. My
  ORIGINAL measurement had used `2>&1`; I measured it correctly and then wrote the claim without
  the redirect.
- 🔴 **A FALLBACK THAT REPORTED A CLEAN RUN FOR A TIER THAT NEVER RAN, TWICE, GETTING WORSE.**
  v1: `nix log` on an unbuilt derivation exits 1 but `>` has already truncated the file, so
  `grep -c 'panic: test timed out'` prints a reassuring **0**. v2 (my fix): guarded that, but left
  `DRV=$(nix path-info …)` unguarded — an empty `$DRV` makes **`nix log ""` resolve as `.` and
  print the cwd flake's DEFAULT PACKAGE log**, so the auditor greps a FOREIGN log reading
  `RESULT: PASS (exit=0)`. **Silence became an affirmative false green.** Both now guarded, with a
  measured control.
- 🔴 **A GUARD THAT ASSERTS THE COMMAND CANNOT SEE A MISSING FLAG.** `test_audit_dispatch.py`
  pinned the `nix build …pytests` substring and stayed green for the whole life of the missing
  `-L`. Replaced with a scan over EVERY emitted `nix build` line. Mutation-controlled: stripping
  the flags fails with **that guard's own message**, and a `.pytests`→`.PYTESTS` rename was the
  positive control proving the harness reached those lines at all.
- 🔴 **A CLAIM IN A LEDGER PROPAGATES AS AN ASSERTION.** Round 1's claims block on `#1133` said
  `--no-link` "is stated with its justification". No such prose existed — I described the fix in a
  commit message and never wrote it into the file. `audit-dispatch.py` REPRINTS the claims block
  into the next round's brief, so a false entry is served to the next auditor as established fact.
  Caught only because round 2 grepped instead of believing the ledger.
- ⚠ **I ran `bash -n` on a Python file and reported a syntax error** while checking someone else's
  script. The shebang is `#!/usr/bin/env python3`. Wrong instrument, confidently reported — the
  exact class I spent the session cataloguing, committed while auditing.
- ⚠ **CARRIED FORWARD from a REPLACE section so it is not lost: the "same derivation" correction.**
  An earlier revision of this doc said a sibling session was building "the same derivation"
  concurrently with mine. It was building `devrc-mergegate-1073`'s tree — a **different source and
  therefore a different `.drv`**, i.e. the same check ATTRIBUTE, not the same derivation. nix takes
  a per-derivation lock, so the original wording described something that cannot happen. Store-level
  contention is real; **no surviving artefact measures the overlap**, so the attribution is from
  memory and stays unproven. The greens stand on their own logs regardless.
- **`ship.sh`'s verdict is a claim about GIT parity, and it is not wrong to say so** — it reported
  `2 hosts compared, both at ec102d00` while the two hosts ran different code, because the
  divergence lived entirely in one host's uncommitted tree. Git parity is not host parity, and
  nothing in the toolchain sees that gap.

- 🔴 **A HANDOFF'S `State now` GOES STALE IN THE DIRECTION OF "ALREADY DONE", NOT ONLY
  "STILL BROKEN" — and this session hit BOTH in one sitting.** Rank 2 was already closed by
  another session, and the workbench had moved OFF the branch the doc pinned it to. A resume
  that trusts the status section re-does closed work and mis-scopes open work simultaneously.
  **Measure every ranked item's closing condition before working it, not just its description.**
- 🔴 **A RANKED ITEM CAN NAME INSTANCES WHEN THE CONDITION IS A POPULATION.** Rank 2 named three
  worktrees; closing all three left **137** registered. The item was satisfiable without moving
  the thing it existed to protect against. When an item enumerates, ask what the enumeration is
  a sample OF, and whether the closing condition measures the sample or the population.
- 🔴 **rc 17 RE-OPENED AFTER BEING CLOSED "AT THE OUTPUT" — a converged state is not a latched
  one.** ⚠ **Carried forward from the `State now` line this update replaced, because the lesson
  outlives the status:** an earlier revision closed rc 17 on the INPUT condition (subtree count
  0, `clawgatectl 0.8.18`) and called the OUTPUT closed; a later one corrected that by actually
  RUNNING `drift-check.sh`. **Checking the input and declaring the output closed is the shape
  this thread keeps finding.** That correction was right — and it is still only a reading at an
  instant: nothing converges `nix/pkgs`' foreign source repos, so the condition regrows silently
  the moment that upstream moves. **Treat every "closed" drift condition as a reading with a
  timestamp, not a latch.**
- **`ship.sh` rc 19 is a RACE, not a failure, and the per-host lines say so.** Both hosts pass
  every internal check while landing on different shas, because `origin/main` moved between the
  two fetches. The fix is literally to re-run it. Reading the final verdict alone would suggest
  something was wrong with a host.
- 🔴 **A DRIFT CONDITION AND ITS BLAST RADIUS ARE INDEPENDENT CLAIMS, AND REPORTING ONLY THE
  FIRST OVERSTATES.** rc 17 fired correctly — the laptop's checkout genuinely was stale — but
  the four stale files under `containers/clawgate` were all tests (`.bats`, `_test.go`), which
  cannot reach a compiled binary. So "the laptop was building from stale source" is true and
  "the laptop was running wrong code" is false. Derive the second from the DIFF, never from the
  commit subjects, and state both.
- **`--ff-only` is what makes a cross-host convergence pull safe to do unattended**: it cannot
  conflict and cannot destroy — it fast-forwards or refuses. Paired with re-verifying the tree
  is clean *immediately before* the pull (not in the survey that motivated it) and recording the
  pre-pull sha, the step is fully reversible.

- 🔴 **A `git checkout -- <file>` USED TO RESTORE A MUTATION ALSO REVERTS YOUR UNCOMMITTED FIX,
  AND NOTHING REPORTS IT.** Measured this session: an edit to `SKILL.md` was made, then three
  mutation controls each restored with `git checkout -- <that same file>`. Restore goes to the
  last COMMIT, so the fix left with the mutation. The commit then contained ONE file while its
  message described TWO, and a claims block served the false version to the next auditor.
  🔴 **Every status signal said success**: the suite was green (that prose was unpinned), the
  commit succeeded, `git log` showed what was expected — because you read the branch you landed
  on. It surfaced only by grepping the MERGED TREE for the sentence I believed I had written.
  `claude/RULES.md` already says to restore from a `cp -a` copy for exactly this; the rule was in
  front of me and "it was only mutated" read as an exemption. **Commit before mutating, or
  restore from a copy — and verify a fix landed by CONTENT, never by the commit succeeding.**
- 🔴 **A HAND-RUN MUTATION THAT DOES NOT APPLY REPORTS A FALSE GREEN; THE BATTERY CATCHES IT IN
  ONE RUN.** Two mutation targets in `SKILL.md` were LINE-WRAPPED, so a one-line pattern was a
  no-op and the run printed a clean pass for a mutant that never executed. `mutants-audit-ladder.sh`'s
  `run` asserts the edit applied and prints `MUTATION DID NOT APPLY — result meaningless`. It
  fired on the very first row added. **Mutants belong in the committed battery, not in a comment**
  — the battery's own preamble says the sweeps it replaces "happened in a session scratchpad that
  no longer exists, including the rows that justified adding a pin". 18 → 21 rows.
- 🔴 **A PIN THAT STOPS MID-PARAGRAPH LEAVES THE TAIL FREE TO ARGUE THE OPPOSITE — committed
  twice, the second time four lines under the banner forbidding it.** Measured: with the caveat
  pinned for 2 of its 6 sentences, inverting "not a shortcut out of a converging one" and
  flipping "If in doubt, run the next round" → "STOP" each scored a **fully green 13-test
  suite**. Those are the only clause forbidding the hatch on a converging ladder and the
  default-to-continue instruction. **When the artifact is prose, pin the WHOLE normalised
  paragraph**, and when you fix one constant, check its SIBLING in the same commit.
- 🔴 **AN ADJACENCY CLAIM NEEDS A POSITIONAL GUARD; TWO STRING PINS CANNOT SEE IT.** A "read the
  next paragraph" pointer was silently re-pointed by inserting a paragraph in the gap — both
  pinned texts still present, both pins green. And the first fix compared WHITESPACE-NORMALISED
  text, which collapses newlines, so deleting the blank line that MAKES two paragraphs also
  scored green while rendered markdown merged them. Compare parsed paragraph BLOCKS and assert
  `index + 1`.
- 🔴 **A COUNT QUOTED WITHOUT ITS SCOPE IS UNREPRODUCIBLE EVEN WHEN IT IS TRUE.** "394 passed
  across six modules" was correct and an auditor reproducing a *different* six got 240 and
  reported it unverifiable. Same round: three "393 passed" figures matched no command at all.
  **Name the command or the module set beside any test count.** Related, measured three times in
  one paragraph: a sentence count went "five" (wrong), then a regex splitter said "four" (wrong —
  `THINGS.**` puts the bold marker between the period and the space), then six by hand. **Count
  by reading, not by pattern.**
- ⚠ **A row naming `origin/main` identifies no fixed tree.** A mutation-matrix `BASE` row read
  "origin/main's SKILL.md ... 6 failed, 5 passed"; `origin/main` has moved far past what it meant,
  so the row is unreproducible. Marked NOT REPRODUCIBLE rather than given a fresh number —
  inventing one would be the defect being fixed. **Pin a sha in any row you want re-derivable.**
- **The attribution gate did not fire on this ladder and was not made to.** Payload per round:
  23 → 0 → 12 lines against ~90 scaffolding each. Never two consecutive zeroes, so the gate stayed
  silent while the ladder was plainly auditing its own scaffolding. That is the documented
  structural blind spot for a prose payload, and the reason the stated-criterion stop exists.

## How to verify
```bash
# --- the fleet: the ONE command that carries ranks 1 and 2's closing conditions ---
bash ~/workspace/devrc/scripts/drift-check.sh
# expect rc 0. Read the PER-HOST lines, never the final verdict alone:
#   [workbench] PARITY-RC=0        <- affirmative: rc10 (behind) is a parity-arm code
#   [laptop]    PARITY-RC=0
#   [*] BUILT SOURCE homelab-talos/containers/clawgate is CURRENT ... 22b250be5ae4
#   [*] SRC-RC=0                   <- rc 17 closed on BOTH hosts
#   [srcrepo] compared=2 same=2 differing=0
# ⚠ a non-zero rc here is EXPECTED to recur: nothing converges nix/pkgs' foreign
#   source repos, so rc 17 regrows whenever homelab-talos/tmux-fuzzyclaw upstream moves.

# --- rank 2's closing condition, WITH the positive control (a bare 0 proves nothing) ---
git -C ~/workspace/devrc worktree list --porcelain | grep -E '^worktree ' \
  | grep -cE 'devrc-seq-absent-empty|devrc-auditfix|devrc-ho2$'    # 0  <- the three are gone
git -C ~/workspace/devrc worktree list --porcelain | grep -cE '^worktree '  # 137 <- the real population

# --- the divergence that is STILL OPEN (expect a non-empty diff = still uncommitted) ---
git -C ~/workspace/devrc diff --stat nix/pkgs/default.nix   # inxi/cpu-x, another session's

# --- the three prior merges, by CONTENT (never ancestry — a squash is never an ancestor) ---
git -C ~/workspace/devrc fetch origin main
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/tests/test_server.py \
  | grep -cE '_wait_ops\(spool_dir, "tabs", 1, where=_routed_to\(inst\)\)'    # 2  (#1109)
git -C ~/workspace/devrc show origin/main:scripts/audit-dispatch.py \
  | grep -cE 'NO DERIVATION|NO LOG — never built HERE|EMPTY LOG'             # 3  (#1133)
git -C ~/workspace/devrc show origin/main:claude/skills/audit-pr/SKILL.md \
  | grep -cE 'A DELTA ROUND CANNOT SEE A CLAIM|WHEN THE PAYLOAD IS PROSE'    # 2  (#1133)
```
## Open investigations — live diagnosis state

### 🔴 A stale claim I introduced in `#1023`, still on `main` — fix open as `#1035`
- **Symptom + exact repro:**
  `git -C ~/workspace/devrc show origin/main:nix/pkgs/tools/default.nix | sed -n '22,24p'`
  → `MEASURED at flake.lock's nixpkgs rev 5c680dac9f02, `pkgs.opencode` is 1.18.21 — store
  path /nix/store/iqc8xfx…-opencode-1.18.21`. **One sentence, two halves, disagreeing.**
- **Observed (with values):** `origin/main`'s `flake.lock` pins nixpkgs at **`c27cdad491a9`**
  (read from the lock's `nodes.nixpkgs.locked.rev`), and the store path quoted in that same
  sentence is the one *that* rev produces. So the rev is the only stale half.
- **Ruled out:** not a second occurrence of the version drift — the VERSION and the STORE
  PATH on lines 23–24 are both correct. Only the rev on line 22 is wrong.
- 🔴 **Root cause, and it generalises:** the re-key was line-targeted at exactly the lines
  the pin-surface ledger flagged, and **that ledger only flags VERSION literals**. Line 22
  carries a *rev* and no version, so it was structurally invisible to the detector and
  therefore invisible to a fix driven by the detector. **A fix driven by a detector is only
  as wide as what the detector can see** — the method was sound and its blind spot was
  inherited whole.
- **Next probe:** none needed, the diagnosis is complete. `#1035` is a correct one-line fix
  by another session; merge it when its checks land.

### The Tekton capacity problem — now with numbers, still unowned
- **Symptom + exact repro:** `tekton/devrc-pytests` fails on unrelated commits, blocking
  every open PR rather than catching defects in any of them.
- **Observed (with values):** `#1009`'s post-rebase run failed **at the 60 s bound** —
  `TimeoutError` at `socket.py:720`, `1 failed, 10031 passed` in `721.93s`. A localhost
  round-trip that did not complete in a minute. Independently, `#1041` measured the retained
  `devrc-ci` runs: **17 failed / 6 succeeded — a 26% pass rate**, six failing runs on six
  different commits, **each failing a DIFFERENT single test** out of ~18,555.
- **Ruled out:** not a defect in any of those PRs (six different tests, six different
  commits); not the store-api client bound alone (`#1023` raised it and the failure recurred
  at the new bound).
- **Leading hypothesis:** a ~12-minute xdist suite competing with a saturated cluster. The
  store-api timeout was one of **six** symptoms; raising it fixed one.
- 🔴 **DELIBERATELY NOT FILED as a work item.** It has no closing condition anyone can check
  and no named owner, so a ticket would read as covered while nothing could close it — see
  the object-leak rule. Recorded here as an open, unowned condition instead.

### The store-api load flake is NOT closed, and the 15s→60s fix has been OUTRUN
- **Symptom + exact repro:** `tekton/devrc-pytests` red on
  `TestTheActorComesFromTheTOKEN.test_a_FORGED_actor_in_the_body_is_DISCARDED`, on PRs whose
  diff cannot reach store-api. Hit `#1035` and `#1074` this session.
- **Observed (values):** `TimeoutError: timed out` out of
  `/nix/store/…-python3-3.12.14-env/lib/python3.12/socket.py:720` — a socket read that never
  completed, NOT an assertion about actor/token semantics. `scripts/tests` took **568s, 55% of
  the run** (`devrc-ci-2x7rp`, pipelinerun `Failed` 03:36→04:00Z). `devrc-nodetests` PASSED in
  the same run (1366/1366).
- 🔴 **`HANG_TIMEOUT = 60.0` is already on `main`** (from `#1015` via `#1023`, confirmed by
  content) **and a LOOPBACK read still exhausted it.** The symptom fix did not hold.
- **Ruled out:** *this change* — the identical tree passed the full sandbox tier locally
  (`nix build .#checks.x86_64-linux.{pytests,nodetests}` rc=0), and nodetests passed in CI.
  Not a raised-deadline problem either: 60s of localhost is scheduler starvation, not latency.
- **Leading hypothesis:** unchanged from the earlier entry — a ~10-minute parallel suite
  competing with a saturated node. Tekton capacity, not this repo's file.
- **Next probe:** when it next fires, read `container_cpu_cfs_throttled_periods_total /
  container_cpu_cfs_periods_total` for `namespace=tekton-ci` (the `tekton` skill's CFS-starvation
  signature) rather than average CPU — low mean CPU with a high throttle ratio is the tell.
- **STILL NOT FILED, deliberately.** No closing condition anyone can check, and the `tekton`
  skill records three fixes already REJECTED with measurements (concurrency capping,
  ResourceQuota, `retries`). Do not design a fix from the skill.

### A sibling test carries the SAME ordering race — named, not fixed
- **Symptom:** `test_an_absent_origin_header_is_not_the_same_as_an_empty_one`
  (`scripts/browser-bridge/tests/test_server.py`) unpacks its pair POSITIONALLY and states in
  its own docstring that order between its two rows IS the signal. It issues both commands
  before waiting, so its order is not structurally pinned, and `where=_routed_to(inst)` drops
  foreign rows without ordering the ones that remain.
- **Observed:** never seen to fail. The mechanism is identical to the one that DID fail
  (`#1074`): the emit runs off the critical path after the HTTP response.
- **Ruled out:** routing as a fix — it closes the foreign-row half only. Measured on `#1074`.
- **Why untouched:** changing a passing test on a theory is how the next flake gets introduced.
- **Next probe / fix:** sequence it — wait for the first row, then issue the second, which is
  `_wait_events`' own sanctioned "order pinned structurally" form.

### RESOLVED — the sibling ordering race, and `where=` was never going to close it
- **Was:** `test_an_absent_origin_header_is_not_the_same_as_an_empty_one` issued both `tabs`
  commands, then waited for two rows and unpacked them positionally, while its own docstring
  says order between them is the signal. `emit_cmd_event` runs off the critical path, after the
  HTTP response, so file order was the scheduler's.
- 🔴 **The reason it survived an order-safety pass: `_wait_ops`' docstring said the site's
  `where=_routed_to(inst)` "keeps the order". IT DOES NOT.** A per-row predicate cannot order two
  rows that satisfy it EQUALLY, and this test's two do — same op, same routing key. `where=`
  separates your rows from a NEIGHBOUR's; that is a different hazard with a different remedy.
  Both hazards are now named separately in the test's docstring and in `_wait_ops`'.
- **Fix (`#1109`):** wait for row one, then issue command two — `_wait_events`' own sanctioned
  "order pinned structurally" form. The single routed row returned before the second command
  EXISTS is the first command's, by observation rather than by argument. `pair[0] == absent`
  then asserts the append-only order still holds, so a future regression says so rather than
  surfacing as a bogus attribution failure.
- **CONTROL, run, because a passing test proves nothing about why it passes:** swapping the two
  commands (keeping the sequencing) turns it RED at `absent["session"]` with
  `KeyError: 'session'` — **while `pair[0] == absent` still PASSES**. So the red is the
  assertions being genuinely order-dependent, not the new guard firing: the mutation died for
  the right reason. File restored afterwards, and the checkable form of that claim is: the
  worktree file, the commit, and the built store source are byte-identical.
  ⚠ **This line used to cite `sha256 1b42b227…` as the proof.** That digest names an
  intermediate working-tree state reaching no commit, so no reader can reproduce it. A later
  audit found the retraction had been ADDED under Gotchas while this line still MADE the claim —
  the doc retracting something it also still asserted, ~140 lines apart. **An append-only
  section cannot be corrected by appending a correction to a different section.**
- **Ruled out:** routing as the fix (closes the foreign-row half only — measured on `#1074`);
  and a tighter deadline as a concern — the change **doubles** the budget, one 10 s wait becoming
  two, worst case 10 s → 20 s.
- **Still open:** the verdict of the sandbox tier. See "State now".

### RESOLVED — round 1 of the blind audit found three defects, all in prose I wrote
- **Method note that earned its keep:** the auditor was dispatched BLIND — the diff and the
  checklist, not my conclusions. All three findings are the failure mode the PR exists to close.
- 🔴 **(1) A FALSE HISTORICAL CITATION, introduced by the fix itself.** I wrote that `#1074`'s
  pair reversed "with `where=` already in place". `git show e9f8ce14` refutes it: the flaking
  site was a bare positional `_wait_events(spool_dir, len(ORIGIN_TOKENS))` and `#1074` **added**
  the `where=`. And `where=` did not fix its order either — that site also became
  `sorted(...) == sorted(ORIGIN_TOKENS)`. **The true version is stronger:** two halves, two
  remedies, and a site whose order IS the signal cannot take the sorting one.
- 🔴 **(2) THE GUARD CARRIED THE IDENTICAL RACE.** `test_a_neighbours_row_of_the_same_op_is_not_
  selected_as_one_of_ours` — the test whose whole job is to protect the site I fixed — issued both
  commands before waiting, unpacked `first, second` positionally, and still carried the comment
  `# THE FIX: where= keeps the pair THIS test caused, in order`, the exact sentence the PR
  retracts twice elsewhere. **The retraction had been applied everywhere except the one place
  that most needed it.** Also: my sentence "THE ONE SITE IN THIS FILE THAT UNPACKS A PAIR" was
  wrong on both halves — after my own change the test I named no longer unpacks a pair, and an
  AST walk finds exactly one tuple-unpack site, which is this one. Now sequenced; control re-run
  on it specifically (RED at `first["session"]`, `pair[0] == first` passing).
- 🔴 **(3) THE NEW GUARD'S COMMENT OVER-CLAIMED — in the PR about over-claiming comments.** It
  said `pair[0] == absent` would report a lost sequencing. It cannot: re-fold the commands and
  `absent` becomes whatever landed first, which IS `pair[0]` by construction, so it stays green.
  Narrowed to the append-only-order invariant it really pins, and it now says outright that
  nothing there can detect the sequencing's removal.
- **Independently re-derived before fixing** — the `git show`, the AST walk, and the mutation
  were all re-run here rather than accepted from the agent.

### CLOSED, and the recommendation went stale mid-investigation — the memory-detail WIP
- **What it was:** `ship.sh` was authorised against a workbench tree holding another session's
  uncommitted `nix/graphical.nix`, `nix/pkgs/default.nix`, staged `scripts/memory-detail` and two
  untracked test files, deploying them to the workbench only.
- 🔴 **OWNER FOUND ONLY BY SEARCHING BOTH RUNTIMES** — opencode session
  `ses_fab8bd9e7ffe6En2UiziYXH9Md`, `run=d6cc95d5`, `directory=/home/zach/workspace/devrc` (the
  base clone, **no worktree**). **No Claude Code transcript contains an `Edit`/`Write` to those
  paths** — only mentions. Searching one runtime would have concluded nobody owned it, which is
  the identical finding this doc already recorded for `discord-embed-ext`.
- **Its agent-ledger record carries `pane_id: None`, `window_id: None`, `tmux_pid: None`** — a
  headless dispatch, never attached to a tmux pane, so `session-manager` could not find a window
  and there was no human to notify. The three live opencode windows all carry different session
  ids.
- 🔴 **RECOMMENDATION RETRACTED BEFORE IT WAS ACTED ON.** I recommended opening a PR for their
  work. Between recommending and re-checking, **the owner landed it themselves** —
  `0c0b8794 feat(bar): memory block left-click opens top RAM consumers view` on
  `feat/memory-detail-click`, pushed. Acting on the recommendation would have DUPLICATED their
  work, which is the shared-queue hazard `claim-work` exists for. The state moved under a
  recommendation that was correct when made.
- **Residue:** `nix/pkgs/default.nix` (`inxi`/`cpu-x`) is still uncommitted, so the workbench has
  two packages the laptop lacks.
