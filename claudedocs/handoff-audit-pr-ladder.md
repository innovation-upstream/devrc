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
- **`#1109`** (`fix/sequence-absent-vs-empty-origin-pair`, head **`e4777c58`**) — the rank-1 fix,
  plus a round-1 audit-fix commit. **`#1111`** (`docs/handoff-audit-pr-ladder-1109`) — this doc.
  Both OPEN, neither merged.
- **Both PRs' required Tekton checks (`devrc-pytests`, `devrc-nodetests`) reported SUCCESS** on the
  pre-audit tips. 🔴 `#1109` has been pushed to since, so its checks are running again — a green
  read before `e4777c58` is a claim about `2579e2f3`, not about the tip.
- **Sandbox tiers, run locally ONE AT A TIME against `2579e2f3`**, verdict read from each
  derivation's own log: `pytests` (`gwzds1c6…-devrc-pytests.drv`) → `RESULT: PASS (exit=0)`,
  `PASS 48  FAIL 0`, 0 `panic: test timed out`. `nodetests` (`gmaidwmi…-devrc-nodetests.drv`) →
  `RESULT: PASS (exit=0)`, **FIVE** `# fail 0` blocks: **569 (browser-bridge)**, 508 (dl-router),
  21 (browser-ext), 188 (clickup skill), 134 (discord-embed-ext). ⚠ **An earlier revision of this
  doc said FOUR and omitted the 569** — the browser-bridge one, i.e. the subsystem `#1109`
  changes. Not re-run since `e4777c58`.
- **Dev-host tier at `e4777c58`:** 477 passed across `test_server.py` + the ratchet.
- ⚠ **CORRECTION to this doc's own concurrency claim.** It said a sibling session was building
  "the same derivation". It was building `devrc-mergegate-1073`'s tree, which is a **different
  source and therefore a different `.drv`** — the same check ATTRIBUTE, not the same derivation.
  nix takes a per-derivation lock, so the original wording described something that cannot
  happen. Store-level contention is real; same-derivation contention was not what occurred, and
  no surviving artefact measures the overlap. The greens stand on their own logs.
- 🔴 **DO NOT ASSUME THE BASE CLONE IS ON `main`.** `~/workspace/devrc` was on `main` at session
  start and on **`docs/handoff-bb-resume-0830`** (created from `origin/docs/handoff-browser-bridge-tab-ref`)
  when this doc was first written — another session's live branch in the shared checkout. Caught
  by `git branch --show-current` immediately before the handoff write; without it `handoff_doc.py`
  would have committed onto that branch, silently. ⚠ The sha that branch sat on is NOT recorded
  here on purpose: an earlier revision named `28ff9d2a`, which was its head for ~2 minutes and
  already wrong by the time that revision committed. **The branch name is the durable fact; a
  moving head is not.** Re-check before every write here.
- **PROVENANCE** (merged, verified by content, all seven re-checked by an independent audit):
  `#1035`→`ccb31628`, `#1060`→`31cd214d`, `#1074`→`e9f8ce14`, `#1023`→`8e33bf1d`,
  `#1033`→`70eff59c`, `#1009`→`442bde83`, `#1005`→`aaa5514c`.
- **Base:** `#1109` branched off `2cec1d45`; `origin/main` moved repeatedly during the session.
  ⚠ "No open PR of the 30 touches either file" was true when measured and is **not a durable
  claim** — there are 37+ open PRs now, and `#1109` itself touches both. Re-derive, don't quote.
- ✅ **RANK 2 (drift-check rc 17) IS CLOSED, NOT "unchanged".** Measured:
  `git -C ~/workspace/homelab-talos rev-list --count HEAD..@{upstream} -- containers/clawgate`
  → **0**, and both hosts now run **`clawgatectl 0.8.18`** (`p85k4nyi…`), not 0.8.17. Its own
  closing condition — "it clears itself when that repo is next pulled for a real reason" — was
  met, and an earlier revision of this doc restated it as live **~50 minutes after it had already
  cleared**. That restatement was carried forward from memory without re-measuring, which is the
  exact failure this thread is about.

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
1. **Merge `#1109`, then `#1111`.** (repo: `devrc`.) IN FLIGHT: `devrc#1109`, `devrc#1111`.
   🔴 **Wait for `#1109`'s checks to re-report on `e4777c58`** — the SUCCESS recorded above was
   against `2579e2f3`, before the audit-fix commit. **Closing condition:** both merged and their
   content present in `origin/main`, verified by diffing the files — never by ancestry, since a
   squash merge makes the branch head a permanent non-ancestor. Checked by whoever merges.
   forcing: gate — the two required Tekton contexts on `main` (`enforce_admins: true`), which
   neither PR can merge without.

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

## How to verify
```bash
# --- the sandbox tier. THREE things the earlier version of this block got wrong ---
# (a) `nix build` prints NO build log without -L, so a RESULT line never appears;
# (b) an already-built derivation prints NOTHING at all, so silence is not a verdict;
# (c) `~/workspace/devrc` is the SHARED base clone and may be on another session's branch.
# So: point it at the tree you mean, and read the DERIVATION's own log.
nix build /path/to/the/tree/you/mean#checks.x86_64-linux.pytests --no-link -L
DRV=$(nix path-info --derivation /path/to/the/tree/you/mean#checks.x86_64-linux.pytests)
nix log "$DRV" > /tmp/pytests.log            # works even when the build was cached
grep -n 'RESULT:' /tmp/pytests.log           # RESULT: PASS (exit=0)
grep -c 'panic: test timed out' /tmp/pytests.log   # 0
# repeat for .nodetests — ONE AT A TIME; a combined invocation produces FALSE failures.

# --- #1109 landed by CONTENT, never ancestry (a squash merge is never an ancestor) ---
git -C ~/workspace/devrc fetch origin main
# BOTH pair sites sequence: each waits for row one before issuing command two
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/tests/test_server.py \
  | grep -cE '_wait_ops\(spool_dir, "tabs", 1, where=_routed_to\(inst\)\)'   # 2
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/tests/test_server.py \
  | grep -cE '^\s+(absent, empty|first, second) = _wait_ops'                 # 0
# the ratchet's PIN did NOT move — sequencing adds a wait, it converts nothing
git -C ~/workspace/devrc show origin/main:scripts/tests/test_positional_spool_reader_ratchet.py \
  | grep -m1 'PINNED_POSITIONAL_TOTAL ='                                     # 47

# --- re-derive the bucket counts rather than trusting any prose that quotes them ---
nix develop ~/workspace/devrc -c python3 -c "
import sys, collections
sys.path.insert(0, '/home/zach/workspace/devrc/scripts/tests')
import test_positional_spool_reader_ratchet as R
recs = R.classify_wait_calls(R.TARGET_DIR)
print(len(recs), 'call sites')
for b,c in sorted(collections.Counter(r['bucket'] for r in recs).items()): print(' %3d  %s'%(c,b))
"   # after #1109: 64 sites, _wait_ops where= 5, positional 47

# --- the CONTROL that makes the fix non-vacuous, for EITHER pair site ---
# swap the two _cmd_sess calls, keeping the sequencing, in a .git-free `cp -a` copy.
# Expect RED at the first row's ["session"] with KeyError: 'session', and the
# `pair[0] == <first row>` line still GREEN. Measured on both sites.
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
