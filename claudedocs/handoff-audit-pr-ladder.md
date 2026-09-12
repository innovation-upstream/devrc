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

- Branch / PR: **every PR from this arc is MERGED — NINE of them.** `#1505` (`bc67b177`), `#1507` (`3a0c77dd`), `#1543` (`0b5ee924`), `#1532` (`6fa09466`), `#1533` (`f3e27aa3`), `#1544` (`9e5c348c`), `#1557` (`6395ce1f`), `#1561` (`c0bbd6d9`).
- **Deploy/verify status: BOTH HOSTS CONVERGED AND VERIFIED AT `db7bf3ff`.** ⚠ Point-in-time: `main` merges every few minutes here and has moved on since; re-run `ship.sh` before relying on host state.
- 🔴 **`#1532`, `#1533` and `#1543` were MERGED WITHOUT CI at the operator's explicit instruction** (2026-09-11, worth the risk because the queue was blocking other work). The post-merge behavioural evidence in ranked item 7 is therefore the ONLY evidence those three work. All three checks have now been run against the MERGED tree, including the one that had only ever run on a branch.
- ✅ **`main` was RED three separate times during this arc and is green as of `c0bbd6d9`** (`test_guard_core.py`: 1537 passed). Root cause each time: a handoff doc mentioning a wide tmux kill and not entered in two hand-maintained lists. `#1561` ends it structurally.
- No `clawgate-task:` recorded: `clawgate_handoff.sh resolve` exited **5**. Its positive control answered 8 links for another session, so the board is reachable — but a wrong id also answers 200 with an empty array, so that is not a clean bill of health.

- **`#1287`** (`feat/workhost`) is **CLOSED** with the operator's comparison written on it.
- ✅ **`#1431` is REOPENED (2026-09-10), carrying a comment with a fresh red/green/positive-control matrix. The defect is unchanged and still open; what is closed is the *board lying about it*.** 🔴 This bullet was written by a prior session which said in terms that it must STAY, because the carried-forward pointer is what kept it findable across `State now` replacements — and one update of this doc DROPPED it anyway while re-asserting the superseded claim it had corrected. **It stays.** ⚠ **UPDATE 2026-09-11:** the defect now has a fix open as **`#1533`** (unmerged) — item 1 closes when that merges; **item 2 still needs a human's written dismissal** and no PR can satisfy it. The bullet stays until BOTH are closed.
- 🔴 **ROUND-0 TRIALS 3-5 ARE DONE, AND RANK 1 RESOLVES TO *KEEP THE SECTION, FIX THE ROUTING*. The pair is `ran: 6 · changed the outcome: 3`** — this doc's trials 1-2 (2/2) + my trials 3-5 (**3/0**) + the peer session's round 0 on `#1518` (1/1). Dispatched 2026-09-12T01:29:04Z against `#1523`, `#1518`, `#1510` from base `ffd665fc`; briefs by `scripts/audit-dispatch.py --round 0 <pr>`, one read-only subagent per PR in its own worktree.
- ✅ **TRIAL 3'S FINDING IS FIXED ON `main` — `#1547` MERGED `6ff4d215` at 2026-09-12T03:10:30Z.** The false attribution in `scripts/tests/test_diagnose_disk_accounting.sh` is gone: the banner now states the rule is declared in ONE file and is **self-attributed**, carrying the measurement and its positive control. 🔴 **Verified BY CONTENT, not ancestry** — `git merge-base --is-ancestor` is **false** after a squash merge, forever, and that is not evidence it did not land: `SELF-ATTRIBUTED` → 1 on `origin/main`, the `git log -S` measurement → 1. Target re-run on the merged tree: **231 ok, 0 failures, exit 0**, section 14 still reporting `all 3 cited sha(s) resolve here AND are ancestors of refs/remotes/origin/main`. ⚠ **The published prediction HELD**: `grep -c 'Both files carry the sentence'` is **still 1** after the merge, and the survivor sits directly under *"An earlier version of this paragraph read"* — the count cannot answer that question, only the context can.
- ⚠ **UPDATE 2026-09-12 to the `#1431` line above:** item 1's defect is now FIXED and merged (`#1533` → `f3e27aa3`), with its `QUICK` control re-run against the MERGED tree. **Item 2 still needs a human's written dismissal** and no PR can satisfy it, so the bullet stays.

- ✅ **RANK 1 IS FULLY CLOSED — THE TRIGGER SHIPPED AND THE TRIAL IS RETIRED, BOTH DEPLOYED AND VALIDATED ON BOTH HOSTS.** Two PRs: **`#1565`** (`5dac6ec2`) made `scripts/claude-hooks/audit-pr-nudge.py` route round 0 as **step 1** of the nudge it fires on `gh pr create` — the only moment round 0's question is actionable — and **`#1574`** (`9400f0b5`) replaced the skill's RETIREMENT CONDITION with a **TRIAL RECORD** carrying the pair, its decomposition and the routing finding. `ship.sh` converged both hosts at each step (rc 0, `2 hosts compared`), and the LAN address timed out both times so the nebula fallback was genuinely exercised rather than assumed.
- 🔴 **VALIDATED AT THE CONSUMER, not at the deploy.** Deployed blobs are byte-identical to `origin/main` on BOTH hosts (nudge `f7407994`, skill `e9bf74f3`, same `/nix/store` paths), the deployed nudge was **executed** and routed round 0 in step 1, the deployed skill carries `TRIAL RECORD`/the pair with `RETIREMENT CONDITION` → 0 and the `ON TRIAL` banner → 0, and the dispatcher reading the deployed skill emits a brief carrying the record with `## THE CHECKLIST` → 0. ✅ **And the hook was observed FIRING IN-SESSION with the new text** when `#1574` was created — an end-to-end observation, not a simulation of one.
- 🔴 **ROUND 0 IS NO LONGER ON TRIAL. Do not re-run the trial, do not re-derive the pair, and do not delete the section.** The record lives in `claude/skills/audit-pr/SKILL.md`. Keep reporting `ran: R · changed the outcome: C` per PR — not to re-decide the section, but because it is now the only signal for whether the TRIGGER is working.
- ⚠ **A FOURTH independent instance of the post-decision dispatch, from another session:** `#1568` merged **6 min** after opening (`04:11:19Z`→`04:17:24Z`, re-derived here), so its round-0 audit landed after the merge — recorded in the skill as corroboration and deliberately **NOT** folded into `R`.
- ✅ **`#1573` MERGED (`e4eeecbd`) — ranked item 7 closed, item 12 added, and `#1431`'s `QUICK` control re-run against the MERGED tree** (it had only ever been watched red on `#1533`'s branch). Matrix at `61f41adf`: baseline 1 passed · `QUICK` override 1 failed · `MIN_TESTS=$LOW` 1 failed · floor drift `15`→`9` 1 failed.
- ⚠ **FLEET RE-MEASURED 2026-09-12 AFTER THE OPERATOR RE-RAN `ship.sh`, TWICE, AND THE HOSTS ARE NOT ON ONE SHA — by design, not by fault.** Reading A: laptop `6f1867b1`, workbench+`main` `c0325f83`. Reading B minutes later: laptop `9400f0b5`, workbench+`main` `20664370`. **The laptop ADVANCED between the two**, so it is tracking, not stuck; it simply sits ~1 commit back because `main` merges faster than a ship run completes. Neither missing commit touched anything this arc shipped. 🔴 **Corrects my own earlier framing of "the laptop is one commit behind" as an unfinished ship — one reading could not tell a lagging host from a stuck one; two could.**
- 🔴 **STILL OPEN and NOT closable by any PR: `#1431` item 2** — "a reader confirms in writing that `5ab273bf`'s mis-named control will not be inherited". Item 1 is fixed and merged. This is the only thing traceable to this arc that remains, and it needs a human.

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

🔴 **Numbering PRESERVED — live `claim-work` refs are keyed to it. Identify an item by its SUBJECT, never its rank.**

1. ✅ **FULLY DONE — nothing here to work. The trial closed at `ran: 6 · changed the outcome: 3` (the section STAYS) AND the trigger it called for is shipped, deployed and validated: `#1565` routes round 0 at PR-create time, `#1574` retired the retirement condition.** 🔴 **An earlier revision of this item told the next session to "delete the RETIREMENT CONDITION … recording the pair" — that is DONE, and an instruction to do already-finished work is the "stale in the direction of ALREADY DONE" hazard this very doc catalogues.** If you want the record, read `claude/skills/audit-pr/SKILL.md`; do not re-derive it. forcing: none
2. ✅ **DONE — operator DECIDED 2026-09-11: IMPLEMENT §10. `#1532` is MERGED (`6fa09466`).** A shared-surface diff now REFUSES to produce a scoped verdict (exit 4, naming the path and pointing at `scripts/gate.sh --tier both`) rather than under-running silently. Re-measured first-hand at `018e483b` — and **the item's own headline figure was stale**: it said *"a `testlib` change runs 1 of 331 test files"*; the real range across all 24 `scripts/testlib/` modules is **0–10, median 1**. The substance was right and understated: `mockbin.py` selected **10** while **69** files across **7** targets reference it, and `gitenv.py` selected **2** while spanning **8**. 🔴 **The zero-select cases were already SAFE** (exit 4, verified) — the dangerous ones are the middle, because a run that executes *something* prints `RESULT: PASS`. ⚠ Deliberately NOT covered, and now tracked as item 8: `scripts/scoped-tests.sh` itself is not a trigger, so a change to the mapper is still validated by the mapper. forcing: none
3. 🔴 **CLOSED — `ship.sh`'s laptop fallback WORKS; my own item was false.** I wrote "the nebula address is used to IDENTIFY the host and never as an SSH fallback" after hitting a LAN timeout early in this arc, and `#1439` had shipped the fallback by the time I wrote it down. Worse, I passed `LAPTOP_SSH=` as an override on BOTH later ship runs out of habit, so I never tested it. **Measured 2026-09-11 with the override removed** (`env -u LAPTOP_SSH ship.sh`): `ship: zach@192.168.50.155 did not answer — falling back to zach@10.42.0.100 for laptop`, then `VERIFIED`. `host-role.sh:120` emits both targets and `ship.sh:545` picks with `first_reachable_ssh`. A prior session had already struck this item; that session was right and I was about to re-assert it. **Do not re-open.** ⚠ **A SECOND session (2026-09-11) independently re-derived the same finding** before reading this line — two sessions have now spent time refuting the same false item, which is the cost of leaving a struck item phrased as a task. forcing: none
4. **Track two — run the algorithm on `audit-pr/SKILL.md` itself** (**34,112 B measured at `6395ce1f`** — `git show origin/main:claude/skills/audit-pr/SKILL.md | wc -c`; ⚠ this item read "~27 KB" for several revisions, understating it, which makes it more urgent not less — almost entirely accreted from prior rounds' findings, i.e. the highest-scrutiny "requirements from smart people" class). Deferred by operator sequencing until the trial count resolves. forcing: none
5. **`1e844f1e`** — another session's cairn handoff commit, pushed but unmerged, parked on `origin/feat/audit-pr-round-0-algorithm`. Not this arc's to merge; flagged so it is not mistaken for dead. forcing: none
6. ✅ **DONE — `#1543` merged (`0b5ee924`), `main` un-redded.** It was red on two `guard_core` tests from two handoff docs classified in NEITHER of two separate allowlists.
   forcing: none
7. ✅ **DONE (2026-09-12) — shipped AND verified. Both hosts converged at `db7bf3ff`.**
   🔴 **The operator explicitly chose to MERGE WITHOUT CI** (2026-09-11, worth the
   risk because the queue was blocking other work), so `#1532`, `#1533` and `#1543`
   landed on **unread checks** — which makes the post-merge behavioural evidence
   below the only evidence there is. All three checks this item named have now been
   run **against the merged tree**, not a branch:
   (a) a `scripts/testlib/mockbin.py` edit exits **4** naming
   `trigger: scripts/testlib/mockbin.py <- scripts/testlib/*`;
   (b) an ordinary `scripts/dl-router/server.py` edit still scopes (**1 of 337**,
   exit 0);
   (c) re-run at `61f41adf`, the `#1431` control matrix is baseline **1 passed**,
   `QUICK` override **1 failed**, `MIN_TESTS=$LOW` **1 failed**, floor drift
   `15`→`9` **1 failed**, battery restored byte-identical.
   ⚠ **(c) was the gap**: it had only ever been run on `#1533`'s branch, and a
   branch result is not evidence about the tree the merge created. `ship.sh` also
   hit **rc 19** on its first pass — the documented race where `origin/main` moves
   between the two hosts' fetches — and the re-run landed both on one sha.
   ⚠ **"Deployed and green" is a point-in-time reading.** `main` merges every few
   minutes here; the hosts sit at `db7bf3ff` while `main` has already moved on.
   forcing: none
8. **Decide whether `scripts/scoped-tests.sh` itself should be a shared-surface trigger.** Today it is not, so a change to the mapper is validated by the mapper. Flagged on `#1532` rather than added, because it is outside the list the operator approved.
   forcing: none
9. **`#1431` item 2 needs a human's written dismissal** — item 1 closes when `#1533` merges, but item 2 ("a reader confirms in writing that `5ab273bf`'s mis-named control will not be inherited") can only be satisfied by a person.
   forcing: none
10. ✅ **DONE — `#1547` MERGED (`6ff4d215`, 2026-09-12T03:10:30Z).** Trial 3's finding is fixed on `main`: the banner in `scripts/tests/test_diagnose_disk_accounting.sh` now states the rule is declared in ONE file and is **self-attributed**, with the measurement and its positive control written beside it. Verified BY CONTENT (ancestry is false after a squash, forever, and proves nothing); target re-run on the merged tree — 231 ok, 0 failures, section 14 green. ⚠ **`grep -c 'Both files carry the sentence'` is STILL 1** and that is correct — the correction quotes the old wording, so the survivor must sit under *"An earlier version of this paragraph read"*. **Read the context, never the count.** forcing: none
11. **The vetr DMARC monitor is laptop-only and rebuild-fragile.** Found by round-0 trial 5, re-verified independently: `nix/home.nix`'s `dmarc-watch` block makes the UNIT declarative while `ExecStart` points at `${workspace}/scratch/vetr/scripts/dmarc-alert.py` and a `~/.config/vetr/cloudflare-dns.env` credential that **nothing manages**. That path exists on the laptop (timer live) and **not on the workbench**, where the service is emitted as a store symlink and the timer is gated off. Either give the vetr scripts a managed home or state in the block's comment that this monitor is laptop-local. Also fix that comment's claim that a hand `systemctl --user start` "still works" — on the workbench it is `203/EXEC` plus a `notify-failure@` toast. 🔴 **Belongs to `claudedocs/handoff-dmarc-enforcement-and-spoofing.md`, not this ladder** — move it there rather than working it here. forcing: regression — a spoofing monitor with a verified true positive that will silently not exist on a rebuilt host
12. ✅ **DONE (2026-09-12) — `#1561` MERGED (`c0bbd6d9`) and shipped: the kill scanners no longer read `claudedocs/`.** They red-ed `main` for the whole repo **six times in about two hours**, from at least four sessions, and *every* offender was prose in a handoff doc — one quoting the scanner's own `offenders=` output while documenting the failure, and one session's own merged handoff (`#1556`, "eliding it was not enough"). Each fix was itself a doc, so each fix could trip the next round. 🔴 **What it does NOT weaken:** the diff is ONE file (the test module); `bash-guard.py`, the hook that actually gates the Bash tool, is untouched and was verified on the shipped tree **by content, not exit code** — still `permissionDecision: "deny"` for a wide kill, silent on a harmless command. A markdown file executes nothing; every executable tree is scanned exactly as before. One predicate `_is_prose_only()` now serves BOTH scanners (they had separate lists, which is why one root cause held `main` red on two tests). Mutants: dropping the predicate from the ledger scan → red; widening the exemption to swallow `scripts/` → red. forcing: none

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
- 🔴 **A RANK NUMBER IS NOT A STABLE HANDLE, AND THIS DOC SAYS IT IS.** The old list declared
  *"Numbering is STABLE — the rank is half a `claim-work` slug's identity"*, and then `#1497`
  (`e1cd9a38`) **REPLACED the whole 16-item list with a 5-item one**. So `audit-pr-ladder-16`
  and `audit-pr-ladder-14` — slugs `claim-work` still derives, and which a session was handed
  as its instructions on 2026-09-10 — now name items that **do not exist in this doc**. The
  work was still open and still findable, but only because the *investigation blocks* survived;
  the queue entries did not. **Identify an item by its SUBJECT (`#1431`, `#1287`), never by its
  rank**, and treat a rank in a kickoff message as possibly pointing at a superseded list.
  ⚠ Both readings of a replaced list are wrong in opposite directions: the numbers can go
  stale (this case) *and* a resurrected item can re-open settled work (rank 4 of the NEW list,
  struck through above, told the next session to re-do what `#1439` already shipped and
  deployed). **One list replacement produced both failures at once.**
- 🔴 **THE BASE CLONE MOVED UNDER A LIVE SESSION, AND NOTHING ANNOUNCED IT.** This session read
  the handoff at `4ab87a64` (1369 lines, 16 ranked items) and later found the same working copy
  clean at `cc278b7a` (1357 lines, 5 items) — another session had pulled `~/workspace/devrc`
  mid-run. Everything downstream of that first read was reasoning about a superseded document.
  **A doc you read at the start of a session is a snapshot, not a subscription** — re-derive the
  sha before acting on a section you read a long time ago, and prefer a worktree pinned to an
  explicit ref for anything you intend to EDIT.
- ⚠ **The zsh history-modifier trap fired again, in a loop written to compare doc versions.**
  `git show "$ref:claudedocs/…"` — zsh ate `:c` as a modifier, so every iteration ran
  `git show 4ab87a64laudedocs/…`, printed `fatal:` to stderr, and the captured counts all came
  back **`0`**. Read without the stderr, that is a clean, confident, WRONG table saying no
  version of the doc had a 16-item list. `claude/RULES.md` names this and says **brace it**:
  `${ref}:path`. The tell was a zero that disagreed with something already read by hand.

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
  rows*, which `_PICKER_ROWS` existed to protect — that table was DELETED 2026-09-03 with the
  live-config guards; the property is now pinned only by
  `test_naming_tiebreak_does_not_reach_the_picker` and
  `test_declared_owner_does_not_reach_the_picker`) and `_attribute` wants precision. So the config
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

- 🔴 **rc 17 RECURRED WITHIN HOURS, ON BOTH HOSTS, AND THE SECOND INSTANCE HAD REAL BLAST
  RADIUS — the prediction in this doc was right and the first instance's harmlessness was
  luck.** The earlier recurrence touched only `.bats`/`_test.go` files, so the binary was
  unaffected; the later one touched **`cmd/clawgatectl/client.go`**, so both hosts were building
  `clawgatectl` without the `#468` deeplink fix that `0.8.20` carries. **Read the DIFF every
  time**: "rc 17 fired" and "the binary is wrong" are independent claims, and the second one is
  the reason to act. Both hosts are now at `eed7db5a` running `clawgatectl-0.8.20`.
- 🔴 **`pull.rebase = true` MAKES `git pull --ff-only` PRINT A FAILURE IT DID NOT SUFFER.**
  Measured on the workbench: `error: cannot pull with rebase: You have unstaged changes.` — and
  HEAD moved anyway. The reflog is the arbiter and said `merge origin/trunk: Fast-forward`. **A
  loud error is not evidence the operation failed**; read `git reflog`, not the message. The
  tracked modifications in that tree were byte-identical before and after (md5 of
  `status --porcelain`, taken both sides).
- 🔴 **AND THE `| tail` TRAP AGAIN, IN THE SAME COMMAND.** `git pull … | tail -3; echo
  "PULL_RC=$?"` printed `PULL_RC=0` — that is **`tail`'s** status, and it happened to agree with
  a success it could not have observed. This is documented in `claude/RULES.md` and in this very
  doc, and was still reproduced by habit at the moment its answer mattered. **The defence that
  works is never quoting a piped `$?`, not remembering the rule.**
- ⚠ **SAME VERSION, SAME SUBTREE COMMIT, DIFFERENT STORE HASH — and that is not a fault.**
  Both hosts run `clawgatectl-0.8.20` from identical subtree tree OIDs (`drift-check`:
  `compared=2 same=2 differing=0`), yet the store paths differ, because the workbench holds an
  UNTRACKED file inside the built source dir (`containers/clawgate/e2e/live-verify-0820.mjs`,
  another session's). The build reads the TREE, not the commit. `drift-check` reports this as
  `DIRTY` and never as drift, which is the right call — but a store-path comparison across hosts
  will disagree with a commit comparison, and the commit is not the thing being built.
- 🔴 **THE STORE-API FLAKE FAILED TWO DOCS-ONLY PRs IN ONE SESSION, ON TWO DIFFERENT TESTS.**
  `#1178` died on `TestTheBackstopNeverSendsASecondResponse` and `#1191` on
  `TestAHungRoundTripSAYSWhichSideBlocked.test_a_stall_in_the_FSYNC_region_is_NAMED` — both in
  `scripts/tests/test_subsystem_store_api.py`, both on diffs consisting of ONE `claudedocs/`
  file, which cannot reach that code. Targets took **464s** and **530s**. Each passed **3/3
  locally in ~5s**, with `--collect-only` confirming the CI-failing test was actually selected.
  🔴 **The second one is self-diagnosing and worth quoting**, because it tells the next reader
  what to conclude: *"the server never reached the stall site, so the hang under test was NOT
  the one this test set up — the report below would be about some other mechanism"*. The test
  detected that its own SETUP had not taken effect under load. Contrast `#1178`, whose message
  said the OPPOSITE of its values (`assert 0 == 1`, `raw == b''` under "a SECOND complete
  response followed"). **A self-diagnosing assertion is worth writing: one of these two cost a
  diagnosis and the other handed it over.** Recorded as evidence the Tekton-capacity condition
  is routine, not occasional — still deliberately NOT filed, for want of a closing condition.
- 🔴 **THE LAST ACT OF CLOSING A RANKED ITEM IS UPDATING THE LIST, AND IT IS THE ONE MOST
  LIKELY TO BE SKIPPED.** `#1185` merged and shipped while rank 4 still read OPEN in this doc —
  the exact duplicate-work hazard that rank 1's own update had fixed hours earlier in the same
  session. A merged PR is invisible to `/resume`; the ranked list is what it reads. **The work
  is not done when the PR merges; it is done when the queue says so.**

- 🔴 **rc 17 RECURRED THREE TIMES IN ONE SESSION, AND THE FIRST INSTANCE'S HARMLESSNESS WAS
  LUCK.** (1) laptop, 2 commits, all `.bats`/`_test.go` — binary unaffected. (2) BOTH hosts, 1
  commit touching `cmd/clawgatectl/client.go` — both were building without the `#468` deeplink
  fix. (3) laptop again, 2 commits touching `client.go` + `internal/ui/components.go` — missing
  `0.8.21`. **Two of three had real blast radius.** The rule that worked every time: read the
  DIFF, excluding `_test.go`/`.bats`/`tests/`, and never the commit subjects. A clean
  `drift-check` here has a shelf life of HOURS — nothing converges `nix/pkgs`' foreign source
  repos, so it regrows on the next `homelab-talos` commit.
- 🔴 **"QUIET WINDOW" FAILED AS A PREDICTOR ONCE AND WORKED ONCE — AND THE DIFFERENCE WAS WHEN I
  SAMPLED.** Attempt 3 was launched at contention 1 and FAILED; the run executed 01:48–02:08Z, by
  which time contention was back to 4. Attempt 4 launched at contention 1 and PASSED — with
  contention **also back to 4 by completion**. So the completion-time reading is the wrong
  instrument in both directions: what matters is contention DURING the fsync-heavy window, which
  neither sample sees. A measurement taken minutes before the act is a hypothesis about now.
- 🔴 **AN ARMED AUTO-MERGE IS A LANDMINE FOR WHOEVER OPENS BREAK-GLASS — INCLUDING SOMEONE
  ELSE'S PR.** Pre-flight for the break-glass found `#1169` (another session's, docs-only) with
  auto-merge **ARMED and its required check RED**, failing on the SAME test as mine. Opening the
  protection window would have merged it past its gate, silently, inside my operation. That is
  not an authorisation I had. 🔴 **Re-measure `gh pr list --json autoMergeRequest` immediately
  before the DELETE, never in the survey that motivated it** — on re-check hours later it had
  gone `CONFLICTING`, which neutralises it (auto-merge cannot land a conflicted PR) but could
  reverse the moment its author resolves the conflict.
- 🔴 **I ARMED THAT LANDMINE MYSELF AND HAD TO DISARM IT.** Auto-merge on a permanently-red head
  does NOTHING useful — nothing re-triggers the gate by itself — so it buys no progress and
  leaves exactly the hazard above. If the head is red, disarm rather than "leave it ready".
- 🔴 **`pull.rebase = true` MAKES `git pull --ff-only` PRINT A FAILURE IT DID NOT SUFFER.**
  `error: cannot pull with rebase: You have unstaged changes.` — and HEAD fast-forwarded anyway.
  **`git reflog` is the arbiter** (`merge origin/trunk: Fast-forward`); the message is not. The
  other session's tracked modifications were byte-identical either side (md5 of
  `status --porcelain`, taken both times).
- 🔴 **THE `| tail` TRAP, HIT AGAIN IN THAT SAME COMMAND — and it AGREED with the truth, which is
  worse.** `git pull … | tail -3; echo "PULL_RC=$?"` printed `PULL_RC=0`: `tail`'s status, for an
  operation whose real outcome it could not observe. It happened to be right. **A piped `$?` that
  agrees is not evidence; never quote one.**
- 🔴 **THE STORE-API FLAKE IS DIAGNOSED, NOT MYSTERIOUS — READ `scripts/ci-repro/README.md`
  BEFORE RE-PUSHING.** It states the mechanism (`_replace_bytes` fsyncs inside the request before
  the response is written; `devrc-ci` is pinned to one node so stacked runs contend on one disk)
  and states outright that it hits **docs-only PRs**. Four failures this effort across two PRs and
  three different tests, targets 464–530s, every one passing 3/3 locally in ~5s with a
  `--collect-only` positive control. Do not re-derive this; do not debug your diff against it.
- ⚠ **SAME VERSION, SAME SUBTREE COMMIT, DIFFERENT STORE HASH — not a fault.** The workbench held
  an UNTRACKED file inside the built source dir (`containers/clawgate/e2e/live-verify-0820.mjs`).
  The build reads the TREE, not the commit; `drift-check` reports it `DIRTY` and never as drift.
- 🔴 **THE LAST ACT OF CLOSING A RANKED ITEM IS UPDATING THE LIST, AND IT IS THE ONE MOST LIKELY
  TO BE SKIPPED.** `#1185` merged and shipped while rank 4 still read OPEN — the exact
  duplicate-work hazard rank 1's own update had fixed hours earlier in the same session. A merged
  PR is invisible to `/resume`; the ranked list is what it reads.

- 🔴 **`audit-dispatch.py <pr> --round 2` needs a round-1 `audit-claims` block ON THE PR** or it
  refuses (REFUSAL 1, rc 2) rather than degrading a delta into a blind full audit. #1185 has
  one; most PRs do not. Measured across the 2026-08-28→09-06 window: **42 of 309 merged devrc
  PRs carry a block**, 146 blocks total.
- 🔴 **The block surface is issue comments ONLY, in practice.** `gh pr view --json comments`
  cannot see review comments or the PR body — but measured over all 309: review comments **0**,
  reviews **0**, body **0 real**. The body produced exactly one FALSE POSITIVE: an illustrative
  example inside a **four-backtick** wrapper in #958's body, whose range `997375ec..9f638fd4`
  RESOLVES and yields plausible churn (53 lines), so "does the range resolve" cannot detect it.
  Detect by counting unclosed fences before the match.
- 🔴 **Block header semantics — the documented off-by-one:** `audited=<from>..<to>` means
  *from* = the tip that round READ, *to* = the head its FIXES produced. **A round's churn is the
  block's OWN range**, never the gap to the next block. Measuring the gap prints plausible
  numbers and manufactures a fake empty terminal range that reads as a clean round. Correcting
  it moved measurability from 134/18 to 153/1.
- 🔴 **A ladder can define the gate's unit two ways in consecutive rounds.** #1132 classified
  `scripts/testlib/nix_units.py` as scaffolding in round 4 and payload in round 5, while its
  summary claimed it "Stopped on the payload-attribution gate" — it silently uses "zero
  *executable* payload" in one place and "payload lines" in another. This is why rank 7 exists.
- **Why ladders are rare is change TYPE, not discipline or a PR-number drought.** `docs`
  carriers 4/175 = **2.3%**; `fix` 26.8%; `feat` 34.8%. `docs` is 57% of the window and 145 of
  175 are `docs(handoff)`. The carrier-free runs (#1003–#1043, #959–#987, #1084–#1107) each sit
  inside a SINGLE day and are made of handoff docs. **A "block drought above PR #N" claim was
  measured, believed, and then refuted** — #1313 carries a real block; the earlier reading
  sampled 12 PRs that happened to sit entirely inside one such run. Do not re-derive it.
- ⚠ **A measurement of this corpus ages within HOURS.** #1313's block was posted
  `2026-09-05T06:03:38Z` and merged `06:35:05Z` — after a 09-04 run that correctly reported it
  absent. Quote every figure with its run date. Related: a fresh merge returns
  `merged: true` with **`mergedAt: null`**, so filtering on `.mergedAt` mis-classifies it.
- 🔴 **BREAK-GLASS WAS AUTHORISED AND NOT USED — moved here from `State now` so a REPLACE
  cannot delete it.** `#1191` was red three times on the store-api fsync condition; the
  operator authorised the branch-protection break-glass, and a re-check immediately before
  acting made it unnecessary — a rebase onto current `main` in a genuinely quiet window
  passed. **`main` was never unprotected** (verified after: both checks required,
  `enforce_admins=true`). The lesson is the re-check, not the authorisation.
- **`claim-work` is at `scripts/claim-work.sh` / `~/.local/bin/claim-work`, NOT `scripts/claim-work`.**
- **No `clawgate-task:` recorded**: `clawgate_handoff.sh resolve` returned **rc 5** with its
  positive control passing (the same endpoint answered 11 links for another session). Per the
  tool, that is a real reading of the board and NOT proof this session's id is right — an
  unknown id also answers 200 with an empty array. No field written.

- **Round 2's gate, measured (supersedes "sweep did not finish"):** full `scripts/tests` sweep at
  `2eaa3c62`, dev-host tier — **2 failed, 11021 passed, 1 skipped in 3243.54s (54:03)**.
  🔴 **Both failures are NOT attributable to the range**, established by control rather than
  assertion: `test_activity_spool_isolation.py::test_a_non_pytest_target_that_leaks_is_named_and_red`
  and `test_gate_exit_truthfulness.py::test_the_verdict_line_carries_the_exit_code` each **pass in
  isolation at HEAD (70.4s) AND at BASE `90202ce5` (65.8s)** with no wall-time divergence, and
  neither file mentions `audit-dispatch` (independently re-checked: **0 hits each**). The sweep's
  traceback tail shows `SystemExit: 143` (SIGTERM) from `escrow-verify.py`'s own timeout reaper —
  a load artefact of the 54-minute run, i.e. the documented load-flake shape, not an assertion
  failure. **The sandbox tier (`nix build .#checks…`) was NOT run for #1185** — no claim either way.
- 🔴 **AN EXTERNAL PROCESS REMOVES AGENT WORKTREES MID-RUN — it is a `git worktree remove`, not a
  crash.** The round-2 auditor's worktree (`agent-ad35faf31a5c575d2`) vanished while it was still
  working. It is **deregistered**, not merely deleted: `git worktree prune --dry-run -v` reports
  **nothing stale**, so something ran a real remove against a live agent. The audit survived only
  because it finished its base-tree control with `git archive` from the shared clone (a pure read).
  **Consequence for dispatch: an agent's worktree is not a safe place to leave the only copy of
  anything.** Have long-running agents report findings incrementally, or materialise trees with
  `git archive` into the scratchpad instead of relying on the worktree persisting.
- ⚠ **Worktree count is GROWING, not stable: 144 registered (2026-09-06), up from ~120 hours
  earlier in the same session, and NONE are prunable.** The doc's older "137 registered worktrees"
  note is the same condition, still unowned and still without a checkable closing condition — but
  the number moves upward every session that dispatches agents. Several hold named branches
  repo-globally at whatever commit they stopped on.

- 🔴 **`git show $REF:path` in zsh SILENTLY RETURNS THE WRONG BLOB — `:s` is a history
  substitute modifier.** Hit twice in one session while verifying #1342: `git show
  $B:scripts/tests/test_audit_dispatch.py` returned a **24 KB patch** instead of the **509 KB**
  test module, well-formed and with no error. `grep -c '^def test_'` on it read **0** where the
  truth was 119. **Brace it: `git show "${B}:path"`.** The literal-sha spelling is unaffected,
  which is what made the two reads disagree and exposed it. This is the documented zsh trap in
  `claude/RULES.md`, met in the wild.
- 🔴 **A verification that re-uses numbers from the report it is checking is not a check.** In
  the same session a "floors verified OK" was computed by running the repo's formula against
  figures typed out of the agent's own report — arithmetic that could only ever agree with
  itself. The real check reads `m` FROM THE FILE (106 rows) and then applies the formula.
- 🔴 **Choose a negative control that CANNOT appear for a legitimate reason.** Post-merge,
  `grep -c 'Now endswith'` was used to prove the overstated `r18/F1` claim was gone; it returned
  **1**. Not a failed correction — the corrected row *quotes* the old claim inside an explicit
  retraction ("THIS ROW USED TO CLAIM … AND THAT OVERSTATED WHAT IT CLOSED"), which is the
  repo's own convention. A control whose string survives inside the fix is no control.
- **The `audit-claims` ledger mechanism works and is worth using.** A round-1 block was posted
  to #1342 by hand (`audit-dispatch.py 1342 --round 1 --emit-claims --audited <sha>`), and
  `--round 2` then parsed it back and assembled the correct delta range with no refusal. #1316
  measured this mechanism in use on only **42 of 309** merged PRs.
- ⚠ **The fix agent REJECTED two pre-validated one-line fixes, with counter-examples from the
  payload itself** — `"#" not in line` false-REDs on a flake ref (`nix path-info --derivation
  <w>#checks.<system>.<name>`, one line away in the same file) and on `#` inside quotes;
  `re.split(r'[;&|]+', …)` splits inside quotes and on the `&` of `2>&1`. Both would have been
  this ladder's signature shape — wider on one axis, narrower on another. **Handing an agent a
  validated patch and asking it to apply it is how round N+1 gets manufactured; ask it to
  re-derive.** Fixing the class instead closed three further latent holes nobody had found.

- 🔴 **A `/handoff` delta that OMITS `## Next steps (ranked)` leaves the stale queue in place —
  and that queue is the ONLY thing `/resume` reads.** Measured here: the State-now section was
  updated to say ranks 5 and 6 were closed while the ranked list still said "NOT CLOSED" and
  "Fix the three 🟡s … then run round 3", i.e. it instructed the next session to redo merged
  work. The durable-drop warning cannot catch this — omitting a REPLACE section is *by design*
  "leave it alone", so the run is silent. **When an item closes, update the RANKED LIST in the
  same delta, not just the status header.**

- 🔴 **A MUTATION BATTERY KEYED ON *WHICH TEST* FAILED IS BLIND TO EVERY CONTROL THAT SHARES A
  TEST — and it reports that blindness as coverage.** `mutants-audit-dispatch.py` grades a row
  by the SET OF TEST NAMES that must kill it, which is the right unit for a payload mutation and
  the wrong one for #1342's eight parser controls: all eight live in the body of ONE test, so
  eight rows would each report `killed by exactly 1: test_the_cached_build_fallback_…` and be
  indistinguishable from one another *and* from any other assertion in that test firing. The
  battery's own docstring already names this hazard ("a mutant can die to a DIFFERENT test's
  error and be scored as covered while its own assertion is unreachable") — it just could not
  express the finer unit. **Ask what unit your battery discriminates BEFORE reading its greens**:
  a second table matching the failing assertion's own MESSAGE is what these needed.
- 🔴 **A NEGATIVE CONTROL FOR THE HARNESS IS NOT THE SAME AS A POSITIVE CONTROL FOR THE MUTANTS,
  AND ONLY THE FIRST TELLS YOU "ALL KILLED" MEANS ANYTHING.** Nine rows all reporting KILLED is
  exactly what a battery wired to nothing prints if every mutation makes the module fail to
  import. The row that made the other eight readable was one that had to SURVIVE: mutating a
  `shell_code` branch no fixture reaches. It is `T9` in the committed table for that reason —
  a control that is not committed is a control nobody re-runs.
- 🔴 **THE MISCOUNT AGAIN, AND THIS TIME IN THE ITEM DESCRIBING WHAT TO VERIFY.** The ranked item
  and the investigation block both said "six control assertions"; the block holds **5 `assert`
  statements** carrying **8 distinct claims** (a 4-iteration loop is 4 controls, not one). Six
  reconciles with neither reading. That is the fourth instance in this thread of a number this
  effort produced and then re-quoted instead of re-deriving — and the first where the wrong
  number was the *specification of the verification*, which means a session that verified
  exactly six would have stopped two controls short and reported the item closed.
- 🔴 **VERIFY A CONTROL BY MUTATING WHAT IT WATCHES, NEVER THE CONTROL ITSELF.** The item's own
  "Next probe" said to *"mutate each of the six control assertions individually"*. Doing that
  literally proves only that the assertion exists and that pytest runs the file — it cannot
  distinguish a reachable control from one sitting behind an early `return`. The mutation has to
  land on the PARSER, arranged so exactly one control's claim breaks and it is the FIRST to
  fail; the control's own message is then the evidence.

- 🔴 **THE BASE MOVING IS WHAT FOUND THE TWO THINGS A FILE-OVERLAP CHECK COULD NOT.** Gating ranks
  7+11 took three bases in one session (`57319960` → `65d8bfba` → `b508b684`, then `c59752b8`
  before the merge). Re-running because the base moved — not because a file overlapped — is what
  surfaced (a) `94f82796`, which FIXED the `failed=7` I had spent six readings characterising as
  an inherited red, and (b) open PR #1050 editing the SAME `audit-pr/SKILL.md` my rank-7 change
  edits. Neither was findable from my own diff. ⚠ **And the treadmill is real**: `main` moved
  again between the last green and the merge, so the honest close was to gate at a NAMED base,
  state the delta, merge, and then gate real `main` — not to chase a moving tip forever.
- 🔴 **SIX READINGS OF AN INHERITED RED, AND THE ANSWER WAS THAT SOMEONE ELSE FIXED IT.** The
  seven failures reproduced identically on four trees (rank-11 alone dev-host + sandbox,
  integration dev-host + sandbox) AND on a pristine detached worktree at the unmodified base —
  which is exactly the control that made "not mine" checkable rather than asserted, and it was
  worth running. But the *resolution* came from re-fetching, not from more measurement of my own
  tree. **When a red reproduces at an unmodified base, the next move is to look upstream for a
  fix in flight, not to characterise it further.** #1389 already had it in CI with a
  main-without-their-commit control.
- 🔴 **A `--ff-only` BASE-CLONE SYNC THAT REFUSES IS A FINDING, NOT AN OBSTACLE — and the tell was
  UNTRACKED files, not a diverged branch.** `~/workspace/devrc` refused to fast-forward because it
  held **untracked** `nix/system/apply-nebula-relay.sh` and `check-nebula-relays.sh` that
  `#1272`/`01956bf0` had just landed upstream. Measured before touching anything: both local
  copies are strictly SMALLER than the merged ones (205 vs 480, 317 vs 360 lines) with older
  mtimes, and upstream is a structural superset — stale earlier drafts, not newer WIP. **Preserved
  byte-exact to `~/workspace/.wip-preserve-nebula-basclone-2026-09-08/` and NOT deleted**; the
  base clone is left 6 commits behind rather than removing another session's files unilaterally.
  The generalisable half: `--ff-only` refusing on *untracked* paths means upstream now ships a file
  someone was drafting locally — compare the two before assuming either is the good one.
- ⚠ **A PR-body trailer appended after the body is already written lands MID-DOCUMENT.** Appending
  a gate table to a body file that already ended in the `🤖 Generated with` trailer put the
  trailer in the middle. Caught before `gh pr create`; noted because the same shape applies to any
  append onto a file with a footer.

- 🔴 **THE BASE MOVED FOUR TIMES DURING ONE GATE CYCLE, AND EVERY SINGLE RED THIS SESSION WAS
  EXPLAINED BY IT — NOT ONE WAS MINE.** Bases: `39c31521` → `57319960` → `65d8bfba` →
  `b508b684` → `c59752b8` → `27d5028d` → `03d7e0ad`. Three separate reds, three upstream causes:
  (1) `failed=7` from nixpkgs drift (`age-keygen` no longer echoing its input, `opencode`
  1.18.29 vs pins at 1.18.21) — fixed upstream by `94f82796` **while I was measuring it**;
  (2) a 3600s TIMEOUT; (3) a content-gate failure on **another session's** file. **The rule that
  worked every time was the same one: re-run because the BASE MOVED, not because a file
  overlapped.** It is also what surfaced open PR `#1050` editing the same `audit-pr/SKILL.md`
  this session's rank-7 change edits — invisible to any diff-based check.
- 🔴 **SIX READINGS OF AN INHERITED RED, AND THE ANSWER WAS THAT SOMEONE ELSE HAD FIXED IT.**
  The seven failures reproduced identically on four trees (rank-11 alone dev-host + sandbox,
  integration dev-host + sandbox) AND on a **pristine detached worktree at the unmodified base** —
  that control is what made "not mine" checkable rather than asserted, and it was worth running.
  But the RESOLUTION came from re-fetching, not from more measurement of my own tree.
  **When a red reproduces at an unmodified base, look upstream for a fix in flight before
  characterising it further.** `#1389` already had it in CI with a main-without-their-commit
  control.
- 🔴 **A 3600s TIMEOUT IS NOT AN ASSERTION FAILURE, AND THE DISCRIMINATOR IS THE TARGET'S OWN
  WALL TIME.** The gate died at its budget stalled on
  `scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py`. That target passes **alone
  in 49.94s**, and in **67.18s** under the runner's EXACT plugin set and xdist args
  (`-p testlib.{nolaunch,spool,gitenv,nogit}_plugin -n 8 --dist loadfile`) — reproducing the
  runner's invocation, not just the test file, is what made the second number worth anything.
  ~50× against two clean isolated runs is the load signature; the re-run completed. ⚠ **Two
  passing isolated runs are NOT proof the full suite completes** — the re-run is what proved it,
  and it is also what surfaced the next finding.
- 🔴 **A CLIENT SUBDOMAIN REACHED THIS PUBLIC REPO, THE CONTENT GATE CAUGHT IT, AND THE MERGE
  HAPPENED ANYWAY.** `test_no_client_hostnames.py` fired on
  `claudedocs/handoff-civitai-app-fleet.md` (landed by `6d488a1b`, another session). Already
  redacted on current `origin/main` — verified two ways: the pattern is gone from
  `git show origin/main:<path>`, and the gate passes **18/18** on a pristine `origin/main`
  worktree. 🔴 **But redacting at HEAD does not unpublish it:** CLAUDE.md states all four content
  gates read `git ls-files` and are blind to history, and this repo's history is public. **With
  branch protection declared off, a content gate that fires is a NOTIFICATION, not a barrier.**
  Whether the history needs anything beyond the HEAD fix belongs to that doc's owner —
  `SECRETS.md` → "Dead credentials in reachable history" is the procedure.
- 🔴 **`--ff-only` REFUSING ON *UNTRACKED* PATHS IS A FINDING, NOT AN OBSTACLE.** The base clone
  could not sync because upstream had just landed files someone was drafting locally. **Compare
  the two before assuming either is the good one** — measured here as strictly smaller + older
  mtimes + upstream a structural superset ⇒ stale drafts. Preserve with `cp -a`, never delete
  another session's tree, and report the blockage rather than "fixing" it.
- ⚠ **Appending to a PR-body file that already ends in a trailer puts the trailer MID-DOCUMENT.**
  Caught before `gh pr create`. Applies to any append onto a file with a footer.
- 🔴 **A MERGE COMMIT'S `--stat` SHOWS THE OTHER SIDE'S FILES, WHICH READS AS "MY COMMIT TOUCHED
  THIS".** `git show --stat HEAD` on a merge listed a file from another session's commit, under a
  label saying it was mine. In a repo with shared checkouts — where this doc already records that
  a wrong-branch commit is the SILENT failure — the right response is to stop and read
  `git log --no-merges <base>..HEAD` rather than explain the stat away. It confirmed one
  non-merge commit, mine, one file.

- 🔴 **`drift-check.sh` PRINTED `no drift` (rc 16) WITH THE LAPTOP NEVER EVALUATED — AND THE
  SUMMARY LINE IS NOT WHERE IT SAYS SO.** Both `ship.sh` and `drift-check.sh` derive the laptop's
  ssh target from `LAPTOP_SSH_DEFAULT` in `scripts/lib/host-role.sh`, which is the **LAN** address
  `192.168.50.155` — reachable only same-network. Measured 2026-09-09: it timed out, and the run
  still ended `no drift on the host(s) CHECKED: workbench (local)` + `rc=16`. The script IS honest
  in its body — `[laptop] UNREACHABLE — … This is not a pass`, `[parity] NOT COMPARED`,
  `[srcrepo] NOT COMPARED`, `[tiers] laptop: NOT REPORTED` — but a reader who takes the last line
  gets a clean bill for a fleet of one. **The fix is one env var: `REMOTE_SSH=zach@10.42.0.100`**
  (nebula), which `remote_ssh_of()` honours unconditionally in BOTH scripts. With it: both hosts
  checked, `[parity] AGREE`, `[srcrepo] compared=2 same=2 differing=0`. ⚠ The unreachable counter
  was at **3/4** — one more silent run and it would have escalated to rc 13 on its own, so this
  was found one run before the deadman would have found it. Open PR **#1287 `feat/workhost`** is
  exactly this problem (reach a host over whichever path is up) and is unmerged.
- 🔴 **A `home-manager switch` FAILING WITH `home-manager: command not found` IS THE DOCUMENTED
  PROFILE-BLANKING, NOT A BROKEN TOOLCHAIN — and the discriminator is an mtime correlation, not a
  `which`.** `ship.sh` rc 9 on the workbench; `command -v home-manager` immediately after
  resolved fine (`~/.nix-profile/bin/home-manager`). `~/.local/state/nix/profiles/` showed
  generations **2103 at 00:41:36** and **2104 at 00:41:45** bracketing the run — a *concurrent*
  switch (another session), whose intermediate generation drops every `home.packages` binary for
  ~1s, and a bare-name invocation inside that window dies. **Check for an in-flight switch before
  re-running, and re-run rather than debugging**: the second attempt was rc 0. Resolving PIDs via
  `/proc/<pid>/cmdline` and skipping the pgrep itself is the read that answers "is one running";
  never let a `-f` pattern reach `pkill`.
- ⚠ **`ship.sh` correctly shipped a DIRTY workbench, and said why.** It classified the 1 untracked
  path against **162 nix-read paths derived from 29 nix files**, found 0 hits, and stated *"NO
  dirty path is read by nix — what was built/deployed IS origin/main"*. Dirty is not a blocker;
  dirty **in a nix-read path** is. That distinction is the difference between a skipped host and a
  correct deploy.

- 🔴 **THIS SESSION NEVER READ THIS DOC UNTIL THE OPERATOR ASKED, and the cost is measurable.**
  It worked the stop rule and the LAN/nebula gotcha — both squarely this effort — without
  opening the handoff, so: **no `claim-work` was taken for any rank**, and **#1287 was not
  consulted** even though this doc names it as the same problem (rank 14). The queue lock only
  works if the doc is read FIRST; a session that starts from a bare operator prompt bypasses it
  entirely, and nothing in the harness catches that. The Stop-hook write-back guard fired at the
  END, which is one bookend, not both.
- 🔴 **CI PENDING IS NOT CI ABSENT, and `no checks reported` is a reading with a shelf life.**
  #1439 was merged after `gh pr checks` said *no checks reported on the branch*; minutes later
  both Tekton checks posted `pending`, and `devrc-pytests` then FAILED. `main` was red ~20
  minutes. **Re-read checks immediately before the merge, not minutes before.**
- 🔴 **The dev-host subset is STRUCTURALLY BLIND to the sandbox tier's hazards, and this is the
  worked example.** `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` guards against
  `/usr/bin/env` because **`env` is absent in the nix build sandbox** — so a dev-host run passes
  it no matter what, and 597 green change-scoped tests said nothing about it. CLAUDE.md's
  deletion of the full-suite ritual makes reading CI the *replacement* signal, not an optional
  one.
- 🔴 **The ladder ran FIVE rounds on #1439 and returned TWO 🔴s, both in its OWN fixes.**
  (a) the fallback was **dead in `ship.sh`** — `remote_ssh_candidates_of` reads `$REMOTE_SSH` to
  detect an operator override and `ship.sh` assigned that same variable its DERIVED default two
  lines earlier, so the probe was skipped every run while 11 lib-level tests and a live probe
  all passed over the no-op; (b) the test written to prove *nothing reaches a live host* ran the
  production `drift-check.sh` against the real `$HOME`, fetching in three shared checkouts,
  calling `gh` authenticated, and **incrementing the real `unreachable-laptop` escalation streak
  once per suite run**. Both are isolation-seam defects: each half correct alone, broken
  together, invisible to any test that loads only one file.
- **The prose-payload stop was exercised again, and the numbers are new evidence for rank 9.**
  Payload lines per round: **124 → 56 → 15 → 20 → 34**, never zero, so §ATTRIBUTION's gate could
  not fire; but rounds 3–5 changed **zero executable payload** (verified: `git diff -U0` over the
  payload files with comment and blank lines stripped is empty). Stopped on the stated criterion
  with the reason named on the PR. ⚠ Reported ONE number under ONE name throughout and said
  explicitly which the stop was NOT taken on — per rank 7's own rule.
- **The `audit-pr` Output section still uses colour-only severity circles.** The operator's font
  renders 🔴/🟡/🟢 as one glyph — *"i can't tell whats what"*. Words (BLOCKER / SHOULD-FIX / NIT)
  are legible; the skill's Output section was left unchanged as out of scope for #1427.
- **`test_drift_check.py`'s command extractor reads loop variables as commands.** `for` and
  `local` are in `_TRANSPARENT`, so stripping them leaves the VARIABLE NAME as token zero.
  `target` is allowlisted in `_PROSE_NOT_COMMANDS`; the blind spot predates #1439 and survived
  only because the file's other loop variable is named `ip`, which passes **by coincidence**
  (`ip` is a real command already in `UNIT_PATH_REQUIREMENTS`). Widening `_command_tokens` is the
  structural fix and was deliberately not bundled.

- 🔴 **A CLOSED issue is a CLAIM, and this arc produced a worked example.** `#1431` went to
  `COMPLETED` with **zero comments** while its defect sat untouched — verified two ways, the
  regex at `test_audit_ladder_stop_rule.py:1179` and an empty `git log --since` over both files.
  A closing condition only works if someone reads it at close time; nothing enforces that, so
  **re-derive a closed item's substance before believing it**, exactly as for a green suite.
- ⚠ **`#1461`'s CI went red on a test its diff could not reach, and the control said flake.**
  `TestTheSpawnHarnessAndThePortRace.test_POSITIVE_CONTROL_a_port_lost_AFTER_it_is_picked_is_
  retried_and_survived`, in `scripts/tests/test_subsystem_store_api.py`; #1461 changed two
  ship-ssh test files. Discriminators run BEFORE merging: it passes locally on that branch
  (14 passed, `-k "PortRace or port_lost"`), and #1458/#1459 were green in CI the same hour, so
  it is neither universally broken nor reachable from the diff. **Scope: verified on the dev
  host, NOT in the sandbox** — a sandbox-only reproduction was never obtained, so "flake" is the
  best-supported reading rather than a proven one. Unrelated to `eeea9025`, which fixes a
  DIFFERENT co-tenant flake (`git maintenance run --auto --detach`) in `test_git_repo_isolation.py`.
- **A red `main` blocks OTHER people's PRs, and that is the cost that is easy to miss.** #1460 —
  nothing to do with this effort — was failing on `test_no_test_writes_a_usr_bin_env_shebang_at_
  runtime` purely because it branched off `605b29ac`. Merging #1461 cleared it. A PR whose red
  check names a test its diff never touched is often inheriting, not breaking: compare its merge
  base against the fix commit before debugging the diff.
- **Docs-only PRs: update the branch, then judge.** #1463's red was inherited from base
  `4dcd1fe2` (pre-fix); `gh pr update-branch` re-based it onto `30a1eb8b`, and it was merged
  without waiting for the re-run because the diff is one markdown file with no code path. Stated
  rather than glossed — it is a judgement under CLAUDE.md's change-scoped policy, not a green.

- 🔴 **A GATE WAS DELIBERATELY SKIPPED, AND THE RECORD SAYS SO RATHER THAN IMPLYING A GREEN.**
  `#1448` (rank 12's queue close, ONE `claudedocs/*.md` file) merged on a **PARTIAL** gate by
  operator decision. Stated in its PR body and repeated here because a merged PR with no gate
  note reads, later, exactly like a gated one. **Ran:** 693 passed across the six modules that
  can actually read a `claudedocs/*.md` — `test_no_client_hostnames`, `test_no_captured_text`,
  `test_no_captured_markup`, `test_no_public_ips`, `test_doc_path_rot`, `test_handoff_doc` — plus
  the node tier (1449/1449) which had completed inside the timed-out run. **NOT run:** the full
  pytest tier and both `nix` check derivations. **Nearest full evidence:** `#1416`, the same file
  and the same kind of edit, four-leg green at base `03d7e0ad` hours earlier. 🔴 **The
  generalisable part is the shape of the note, not the decision:** name the tier that ran, the
  tier that did not, and the nearest full green — a subset reported without those three is
  indistinguishable from a gate.
- 🔴 **TWO 3600s GATE BUDGETS DIED TO BOX SATURATION, AND THE DISCRIMINATOR WAS `/proc`, NOT A
  RE-RUN.** Both runs stalled with **no target completing**, and on a DIFFERENT target each time
  (`scripts/dl-router/tests`, then `scripts/claude-hooks/tests/test_clawgate_task_interview_guard.py`)
  — which is the load signature, since a failed assertion inflates exactly one target and load
  inflates all of them. Measured at the moment it mattered: **load 79.48 on 24 cores**, with three
  OTHER sessions running full suites and `nix build` concurrently (`devrc-gate-base` running
  `run-tests.sh`, session `3495c5a1…` running `nix build …#checks…pytests`, a third
  `run-tests.sh --set hermetic`, plus two agent worktrees). 🔴 **The right response was NOT to
  re-run**: a fourth concurrent suite degrades the other three sessions' runs as much as its own.
  Read `/proc/<pid>/cmdline` + `cwd` for the competing work before deciding, and never let a `-f`
  pattern reach `pkill`. ⚠ The stalled target passing in isolation (49.94s, and 67.18s under the
  runner's exact plugin+xdist args) is **necessary and not sufficient** — it does not prove the
  full suite can complete; only a completed run does.
- 🔴 **I GREPPED THIS DOC FOR MY OWN SENTENCES AND READ "CONDENSED" AS "DELETED".** Checking that
  ranks 7/11/12 survived, exact-string `grep -cF` on the wording I had committed returned **0**
  for ranks 7 and 11 and for one gotcha — and the conclusion "another session dropped my entries"
  was wrong. They were **rewritten shorter, with the numbering intact**; a looser pattern found
  all of them. **A grep counts the string you typed, never the content you mean** — this doc
  already says so twice about other people's numbers, and it was reproduced here on my own.
  🔴 **The real hazard it points at is still live, though:** `Next steps (ranked)` and `State now`
  are REPLACE sections in a doc that **several sessions write concurrently**, so a delta that
  includes either one silently overwrites whatever another session put there since. This update
  deliberately omits BOTH and touches only the append-only `Gotchas`, which is what "omit a
  section and it is left alone" is for.

- 🔴 **`~/workspace/devrc` has concurrent writers, and it bit this session twice.** Another session committed onto local `main`, then switched my feature branch out from under me leaving me on `main` — `bash-guard.py` blocked the commit that would have landed there. Later it committed a handoff doc **on top of my commit, on my own branch**. Nothing was lost (its commit is on `origin/feat/audit-pr-round-0-algorithm`), and the fix was to cut a clean branch at my own sha. **`git branch --show-current` immediately before every commit is not optional here**, and `git reflog` is the one-command diagnosis.
- 🔴 **A test run and a mutation sweep in the SAME worktree corrupt each other.** This session ran a full pytest tier in a worktree and then edited files under it during a fix round; the run had been reading a tree changing beneath it. Its reported `exit 0` was a `| tail` pipeline status, not a verdict. Killed by resolved PID and discarded. Use a worktree per concurrent activity, not per task.
- **A proximity guard is walkable by the very text that fixes the thing.** `#1495`'s v1 asserted "a retraction appears within 20 lines of the figure"; re-asserting the figure in the same header SURVIVED, because the new retraction note satisfied the check. Rebuilt as a two-way pinned COUNT ledger; the mutant now dies. Its first ledger draft was ALSO wrong (guessed 2, actual 1 — the scanner counts lines, not regex matches) and the two-way pin caught that.
- **Round 0's ledger deliberately carries NO add-back percentage.** An earlier draft asked for `deleted: X · re-added: Y (Y/X = Z%)` "from the same `--numstat` command"; `--numstat` gives per-file added/deleted counts and cannot identify a re-added line, so `Y` was undefined by the instrument named. Recorded in the section so it is not re-derived.
- **The merge policy changed mid-session and it matters.** CLAUDE.md's local full-suite ritual before merge is DELETED — it produced 27-50 concurrent full-suite runs on one 24-core box while gating nothing. What replaces it: read CI (advisory, ~42% of reds are noise) and run a change-scoped subset. Measured here: load 60-96 for hours, 36 concurrent `run-tests.sh`, and one full pytest tier that hit the 3600s cap with NO verdict.

- 🔴 **A CLAIM CORRECTED ON ONE SURFACE STAYS FALSE ON THE OTHERS, AND THIS ARC DID IT FIVE TIMES.** The `#1495` ladder existed *because* `scoped-tests.sh` asserted a figure `CLAUDE.md` had retracted. Then: a commit message claimed it had corrected the PR body when no edit was made; the PR body kept a withdrawn "3 of 7" measurement after the docstring dropped it; the cairn entry taught the wrong remedy for a day; and this handoff said `#1495` was "OPEN, unmerged and unaudited" while ranking "audit and merge #1495" as the next step, hours after it merged. **Every one was caught by a sweep, none by reading.** The sharpest instance is ranked item 3 below: I wrote a ranked item from an observation that another PR had already fixed, then passed an override twice that made my own claim untestable, and a DIFFERENT session struck the item before I did. A stale observation plus a habitual workaround is indistinguishable from a live defect. The ladder re-audits the PR; nothing re-audits the artifacts a session writes *about* its own work — the handoff, the store entry, the PR body. Sweep those before ending a session.
- 🔴 **A lesson recorded MID-LADDER records the wrong remedy.** The cairn bullet was written after round 1 and named the count ledger as "the remedy that held"; round 1's own next finding was that it did not hold. Write the durable lesson when the ladder STOPS, not when a fix feels right.
- 🔴 **`git checkout -- <path>` to restore a mutated file reverts the COMMITTED version and destroys uncommitted work.** Done here mid-battery; it wiped the entire fix and was caught only because the final control printed `3 passed` where `2` was expected. `claude/RULES.md` says restore from a `cp -a` copy — this is what that rule is for. **Keep a control whose expected number you know**, or a silent revert reads as a pass.
- **A guard's own remediation text can satisfy the guard.** v1's proximity window was satisfied by the retraction note the same commit added, so the hazard could be re-introduced immediately beside its own fix.

- 🔴 **DO NOT QUOTE THE RETRACTED CONTENTION FIGURE IN A CHURNING DOC — `#1495`'s guard fires on it, correctly.** `test_retracted_contention_figure.py` fails any file carrying the figure that its ledger does not name. This doc quoted it once while DESCRIBING the guard's own history, and `tekton/devrc-pytests` went red on `#1511` — a TRUE positive. 🔴 **The fix is to reword, NOT to add this doc to the ledger:** the ledger pins a sha256 of the normalised text around each occurrence, and a handoff rewritten every session would move that digest constantly, making the guard permanently red — which `claude/RULES.md` says is worse than no gate. The ledger's three entries (`CLAUDE.md`, `handoff-gate-speed-and-ci-signal.md`, `scoped-tests.sh`) are stable prose; this doc is not. ⚠ The guard's failure message offers "add it with its digest" or "delete it" and does not yet name this third case — describing the figure without quoting it. Worth adding when someone next touches that file.
- 🔴 **A `State now` REPLACE WILL EAT ANOTHER SESSION'S DELIBERATE CARRY-FORWARD, AND THE TOOL WARNS YOU IN A LINE THAT IS EASY TO SKIM.** Measured on this doc, 2026-09-11: an update dropped a bullet whose own text read *"This bullet stays (rather than being deleted) because the carried-forward pointer is what kept it findable across three `State now` replacements"* — and in the same edit re-asserted the stale claim that bullet existed to correct (`#1431` CLOSED, when it had been REOPENED the day before). `handoff_doc.py` printed `This replace DROPS 3 line(s) that look DURABLE` and the writer read it only after pushing. **Read the DROPS block BEFORE the confirm, and grep the dropped lines for `stays`/`carried forward`/`do not delete` — a bullet that argues for its own survival is the one a REPLACE is most likely to kill.**

- 🔴 **A REMEDIATION WRITTEN INTO AN ISSUE IS A HYPOTHESIS, NOT A SPEC — and only RUNNING the control it demanded revealed that.** `#1431` prescribed *"have the battery print its effective `MIN_TESTS` and assert on that"*. Implemented exactly as written it closes `MIN_TESTS=$LOW` and **leaves the `QUICK` case GREEN**, because `--print-min-tests` runs with `QUICK` unset so the effective floor honestly IS 15 for that invocation while a `QUICK=1` run floors at 3. **"Effective" is environment-dependent and no probe can enumerate the environments.** The closing half had to be STRUCTURAL: exactly one `MIN_TESTS=` assignment, matched `^\s*` not `^`. The old guard's column-0 anchor is the whole reason an INDENTED override beat it — and the issue itself asserted that a second assignment "fires the `len(literals) == 1` check", which is true only unindented.
- 🔴 **TWO SEPARATE ALLOWLISTS OVER ONE HAZARD KEPT `main` RED ON TWO TESTS FOR ONE ROOT CAUSE.** `_KILL_MENTION_LEDGER` and `quoting_is_the_point` both enumerate files carrying wide-tmux-kill text, and a file classified in one is still an offender to the other. Fixing the first alone left the module red — which is how the second was found. A "one rule, one place" smell nobody has consolidated.
- 🔴 **A RED CHECK ON YOUR PR CAN BE `main`'s RED, AND THE CONTROL IS CHEAP.** `#1533` went red on a test in a directory its diff cannot reach. The discriminating control — the same test on a clean detached `origin/main` worktree — cost one `worktree add` and settled it in two minutes. **Run it before debugging your diff**, and before quoting the documented ~42% noise rate as the explanation: this was neither noise nor the diff.
- 🔴 **THE GUARD THAT FORBIDS A HAND-WRITTEN SHEBANG CAUGHT A NEW TEST FILE ON ITS FIRST CI RUN.** `#1532`'s fixture wrote a stub runner with a literal `#!/bin/sh`; `env` is absent from the nix build sandbox, so such a stub is unrunnable in the tier the merge is gated on. Use `testlib.mockbin.write_exec`, which owns the shebang and refuses a body carrying one. **And re-run the mutation controls after that fix** — the fixture is what the negative control depends on, so an audit fix resets the gate.
- 🔴 **A GUARD TEST THAT ALLOWLISTS FILES MUST BE RE-CONTROLLED AFTER YOU ADD TO IT.** Adding entries to a safety allowlist is exactly the move that silences a guard. The control that settles it: introduce a tracked file that SHOULD offend and confirm the guard still fails, then remove it and confirm green. Both directions, or the additions are unproven.
- 🔴 **`| head -N` DESTROYS A RUNNER'S VERDICT AND HANDS YOU `head`'s EXIT CODE.** A backgrounded `scoped-tests.sh` run was piped through `grep | head -25`; the `RESULT:` line fell outside the first 25 matches, and the reported `exit 0` was `head`'s. The run proved nothing. Documented trap, reproduced anyway, in the command whose verdict mattered.
- 🔴 **`pgrep -f <pattern>` MATCHES THE SHELL RUNNING IT.** Checking whether a background job was alive with `pgrep -f "scoped-tests.sh"` returned "still running" because the pattern appears in the checking command's own argv. It answers about the instrument, not the job.
- 🔴 **THE BASH-GUARD PARSES HEREDOC LINES AS COMMANDS, SO QUOTING A BANNED COMMAND IN A PR BODY IS BLOCKED.** Writing a PR description that quoted a wide tmux kill via `gh pr create --body-file <(cat <<'EOF' …)` was refused by the hook. The remedy is the one the hook prints and the RULES already prefer: write the text with the Write tool and pass `--body-file <path>`.
- ⚠ **A MUTATION STOPPED APPLYING BECAUSE THE FIX'S OWN COMMENT QUOTED THE LITERAL.** `MIN_TESTS=15` became non-unique once the explanatory comment cited it, and the mutator reported `MUTATION DID NOT APPLY (count=2)`. The `1 passed` printed alongside was **meaningless, not a survivor** — re-run against a unique anchor it came back red. An inline mutator without that assert manufactures false greens.
- ⚠ **THE RANKED LIST WAS RENUMBERED TWICE MORE DURING THIS SESSION**, by other sessions. Items this session was handed as "rank 14" and "rank 16" no longer exist under those numbers; the work was found only via the investigation blocks. **Identify an item by its SUBJECT, never by its rank** — and the current list's items 1–5 were carried forward verbatim here rather than re-ranked, because re-ranking silently re-points every live `claim-work` claim.

- 🔴 **AN AUDITOR RE-IMPLEMENTING A MEASUREMENT *FROM ITS METHOD DESCRIPTION* REPRODUCES ITS BUGS AND REPORTS A CONFIDENT **MATCH** — it passed a positive control and was still wrong.** Round-0 trial 4 was told to re-verify `#1518`'s "0 of 16" rather than accept it. It wrote its own extractor *"from the method description, not from their script"*, matched **13 of 13 figures** including a 36.1% control, and certified the requirement as HOLDING. A peer's round 0 found one counterexample and the PR was closed unmerged 5 minutes before trial 4 returned. The inherited flaws were all in the METHOD: a needle requiring a `handoff-` prefix the sessions never write, assistant text truncated at 400 chars while the mentions sit at ~3,100, and `Write`/`Edit` captured as `file_path` only so a hit written INTO a doc was invisible. 🔴 **`claude/RULES.md` names this — *"a control that SHARES the step you doubt is a second sample of the same unknown"* — and the sharing was at the level of the METHOD, which is why it did not look like sharing.** The check that worked was the peer's: **hunt ONE counterexample by hand** rather than re-derive the aggregate. An N-of-13 agreement between two implementations of one flawed method is replication, not corroboration.
- 🔴 **I DISCREDITED A CORRECT AUDITOR TWICE, WITH ZEROS I NEVER CONTROLLED — and the cause was grepping the WRONG FILE of a two-file pair.** A round-2 audit cited `lines[i].endswith("exit 1; }")` and `THIS COMMENT USED TO STATE THE INVERSE` as evidence that `#1185` F1/F3 were fixed. I grepped `scripts/audit-dispatch.py`, got nothing, and published *"that string does not exist on `origin/main`"* — twice, in a doc that teaches instrument discipline. **Both exist in `scripts/tests/test_audit_dispatch.py`**: `endswith("exit 1; }")` twice (`:3036` is the LIVE assertion — the auditor's best proof, which I threw away) and the INVERSE comment at `:3052`. The blocks were explicitly about BOTH files. 🔴 **Two compounding errors: I searched one half of a two-file subject, and I published a negative with NO positive control** — the exact rule this doc repeats. **Before quoting a zero, make the instrument produce a non-zero on a case you know is there; and when a finding names two files, grep both.**
- 🔴 **A FINDING'S VALIDITY CAN DEPEND ON ITS PR'S FATE, AND NOTHING IN THE REPORT SAYS SO.** Trial 4's two remaining findings both evaporated when `#1518` closed: its `handoff-index` subsystem `deletion candidate` was premised on *"this PR removes its last automated consumer"* (it does not — the step stays wired), and its 🟡 against `claude/skills/mailbox/SKILL.md:38` flagged a line as false that is, with the PR closed, **TRUE**. Both read as durable structural findings about the repo. **Ask whether a finding is conditional on the diff LANDING before carrying it forward.**
- 🔴 **A DEPLOY-STATE FINDING HAS A SHELF LIFE IN MINUTES; A STRUCTURAL ONE DOES NOT.** Trial 5 reported 🔴 *"the monitor is DARK on both hosts"*, measured in the window between `#1510`'s merge and the next `home-manager switch`. Re-measured 15 min later the laptop was live and the workbench's timer absent **by design**. Its 🟡 — the rebuild-durability gap — was unaffected. **Check which kind you have before escalating.**
- 🔴 **`claim-work` RETURNED rc 12 "THIS SESSION" FOR A CLAIM HELD BY A DIFFERENT, LIVE SESSION.** The token is `/etc/machine-id` + the git dir, so **two Claude sessions in one base clone read as one owner**. Measured: `audit-pr-ladder-1` and `-2` were held by session `b6315603` (transcript written one second before this one's), which had just opened `#1532`. rc 12 says "carry on", and carrying on would have duplicated a live peer. **In a shared clone rc 12 means "this clone", not "this session": check `when:` and the subject against work you remember doing, and sweep `gh pr list --state open`.** The sweep found `#1532`; the lock could not.
- 🔴 **A FABRICATED ATTRIBUTION SURVIVED TWO ROUNDS BECAUSE A SPECIFIC WRONG ID READS AS MORE CHECKED THAN A VAGUE RIGHT ONE.** Asked to replace "a peer session" with an id, I wrote that `b6315603` *"went on to CLOSE `#1518`"*. **Measured across all devrc transcripts: `b6315603` mentions `1518` ZERO times** (positive controls in the same file: `1532` → 65, `1287` → 59), and the session that closed it is **`a21e60e0-7bc4-41b6-af4e-44c3af9ff3e0`** — the only transcript containing `gh pr close 1518`, at `01:39:27Z`, two seconds before GitHub's `closedAt`. `b6315603` opened `#1532` and closed `#1287`; I conflated two peers. **This was WORSE than the vagueness it replaced**, because a checkable id that is wrong stops the reader looking further — and it was attached to the one piece of evidence rank 1's verdict rests on. **When you upgrade "someone" to an id, grep for the id doing the thing, with a control proving the grep can see that file.**
- 🔴 **THE SHARED BASE CLONE CHANGED BRANCHES UNDER THIS SESSION, NOT JUST COMMITS.** `~/workspace/devrc` was on `main` at session start and on another session's feature branch — with their UNCOMMITTED work — ten minutes later, HEAD having moved three times. **A `--confirm --push` in that tree would have committed onto a peer's branch on top of their dirty files.** `git branch --show-current` immediately before the write is what caught it; the handoff was landed from a worktree pinned to `origin/main` instead.
- 🔴 **A GREP FOR A HAZARD'S NAME COUNTED THE COMMENT WARNING ABOUT IT.** Checking whether round-0 briefs leak the nine correctness axes, `grep -c 'After the fixes'` returned **1** for every brief — the match was inside the load-bearing HTML comment that *documents* the leak. The honest instrument is a column-0 heading match (`grep -c '^## After the fixes'` → **0** in both briefs). ⚠ **And my first "control" for that was not one** — a `--round 1` brief returns 0 for the pattern too, so it cannot show the pattern is ABLE to match. The real control is the skill the briefs are built from, where both headings exist.
- 🔴 **A NEEDLE CAN BE INCAPABLE OF OBSERVING THE THING IT WAS CHOSEN TO DETECT — SPELLED, NOT STRUCTURAL.** I measured `#1532`'s gap with `grep -c 'shared-surface' scripts/scoped-tests.sh` → 0 and reported the refusal absent from `main`. That needle returns **0 even now that `#1532` is fully merged and the refusal is live**, because the implementation spells it `SHARED_SURFACE` / "shared surface". The claim happened to be true when made and the instrument could never have told me otherwise. **Assert the STATE — a symbol, a code path, an exit code — never a word another spelling can defeat.** The honest needles are `SHARED_SURFACE` → 2, `scripts/testlib` → 3, `gate-inventory` → 2, at 467 lines and 7 `exit 4` hits.
- 🔴 **TWO SESSIONS UPDATING ONE HANDOFF DOC PRODUCED A CONFLICT THAT A CLEAN TEXTUAL MERGE WOULD HAVE RESOLVED WRONGLY.** `#1533` merged a 25-line measured block into this file on `main` 36 seconds after my fix commit, and `#1532` merged 6 seconds after it — so a PR that was accurate when written asserted three false statuses by the time anyone read it, and `gh pr view --json mergeable` returned `CONFLICTING`. **Taking my side of that conflict would have deleted a peer's merged correction**; taking theirs would have dropped measurements only my side had. Resolved by **resetting onto current `main` and re-applying my unique content through `handoff_doc.py`**, which merges section-wise, rather than hand-resolving eight hunks. ⚠ `git rerere` recorded a preimage during the aborted merge; cleared with `git rerere clear` so it cannot replay a resolution nobody chose. **A handoff doc is a shared mutable file: re-read `origin/main` immediately before you push, not when you started.**
- 🔴 **A ROUND'S FIX CORRECTS THE SITE THE AUDIT NAMED AND LEAVES THE DOCUMENT'S OTHER COPY STANDING — three consecutive rounds found instances.** Round 1 rewrote a ranked item; `State now` kept asserting the opposite in bold 🔴 **and `State now` is what a resuming session reads first**. Round 2 fixed the pair; round 3 found the back-pointer missing and a third copy of a ledger figure. **This is "one rule, one place" applied to prose: grep the document for the claim's SUBJECT, not the line the audit quoted.**
- ⚠ **Two of three trial PRs were picked as "fresh and unaudited" and both were gone within minutes** — one merged between the survey and the dispatch four minutes later, and `#1509` merged mid-session as a fourth near-miss. **Read `state` and `mergedAt` in the same command that records the baseline**, not in the one that picks candidates.
- **Trial 3's author had already run round 0's step 1 on themselves.** `#1523`'s commit message questions a requirement from its own prior audit round, measures it, and REJECTS the recommendation. Round 0's unique contribution there was narrow — noticing the criterion was not applied to a sibling requirement. Worth knowing before reading `changed the outcome: 0` as evidence the section is inert.

- 🔴 **A BRANCH RESULT IS NOT EVIDENCE ABOUT THE TREE THE MERGE CREATES — and after a merge-without-CI it is the only thing anyone has.** `#1431`'s closing control (`QUICK` override ⇒ the floor guard reddens) had been watched red on `#1533`'s branch and never on `main`. Re-run at `61f41adf` it still reddens, so nothing was wrong — but "verified" was a claim about a branch for a day. **After merging without CI, re-run each behavioural control against the merged tree and say which tree you ran it on.**
- 🔴 **I FLAGGED A STALE RANKED ITEM IN SOMEONE ELSE'S QUEUE AND THEN LEFT MY OWN.** Two turns after reporting that rank 1 still read OPEN for work already merged, ranked item 7 still read *"`ship.sh` has NOT been run"* after I had shipped and verified both hosts. **The last act of closing an item is updating the list, and being the person who just warned about it is no protection at all.**
- ⚠ **A stray scratch file named `.e` silently widened a scoped-run measurement TWICE.** `scoped-tests.sh` maps a changed file to tests naming its basename; a basename of `.e` matches nearly every Python file, so the run reported **312 of 337** selected instead of **1 of 337**. Both readings were of my own leftover file, not of the change under test. **Write scratch output outside the repo, and re-read `git status -s` before trusting a selection count.**

- 🔴 **"BOTH HOSTS CONVERGED" DECAYS WITHIN MINUTES IN THIS REPO, SO IT IS A READING WITH A TIMESTAMP AND NEVER A PROPERTY.** `main` merges every few minutes; a `ship.sh` run takes longer than that, so the resting state of the fleet is **one host ~1 commit back**, not equality. `ship.sh` rc 19 says the same thing from the inside — it fires when `origin/main` moves between the two legs' fetches. **The actionable question is never "are the shas equal" but "does the DIFF between them touch anything that matters"** — measured here twice, and both times it did not. A single reading cannot distinguish a LAGGING host from a STUCK one; two readings can, and the tell is whether the trailing host's own sha advanced.
- ⚠ **THE `| head` EXIT-CODE TRAP, AGAIN, IN THE COMMAND WHOSE ANSWER DECIDED A BRANCH.** `clawgate_handoff.sh field <doc> | head -3; echo "field-rc=$?"` printed `0` — `head`'s status — which reads as "a task field is already present, leave it alone". Captured properly (`out=$(…); rc=$?`) it is **1**, meaning no field. The handoff skill states this trap explicitly two paragraphs above the command, and it was still reproduced. **The defence that works is never quoting a piped `$?`, not remembering the rule.**

- 🔴 **THE TRIGGER ALREADY EXISTED — THE FIX WAS ONE RULE IN ONE PLACE, NOT THE NEW MACHINERY THE PLAN NAMED.** Rank 1's `Next probe` offered two options: a `gh pr create`-time dispatch or a poller over PRs younger than 15 minutes. Before building either I checked whether anything already fired at PR birth, and `scripts/claude-hooks/audit-pr-nudge.py` has fired on `gh pr create` all along — it had simply never mentioned round 0, naming only `/audit-pr <n>`, which defaults to round 1. So the whole routing defect was a missing sentence in a hook that was already live, already registered and already deployed to both hosts. **Before building a trigger, grep for one: a "missing mechanism" is often a mechanism with the wrong message.** The poller would have been a second system doing what the first already did at a better moment.
- 🔴 **A NEW TEST FILE THE GATE DOES NOT *NAME* IS A SUITE NOTHING RUNS — and CI caught it where I had not.** `#1565` added `scripts/claude-hooks/tests/test_audit_pr_nudge.py` and I reported its 10 tests green from a by-path run. CI then went red on ONE test of 22,375 —
  `test_hook_tests_dir_collects.py::test_the_classifier_matches_run_tests_shs_own_two_lists` — whose message says it outright: *"A collectable suite here that the gate does not name is a suite NOTHING runs."* `scripts/claude-hooks/tests/` is pinned two ways, `conftest.py`'s classification against `run-tests.sh`'s `HOOK_TESTS` **and** `HERMETIC_TARGETS`, and my file was in neither. **A by-path pytest run is true and is not a gate run**; the gate runs what it NAMES. Adding a target needs BOTH the `HERMETIC_TARGETS` entry and a `TARGET_FLOORS` row, the floor taken from the gate's own documented rule (`m - min(50, max(1, m/20))`, so 10 → 9) rather than from arithmetic you like. 🔴 **This is the live version of CLAUDE.md's "a NEW file must be `git add`ed or the flake silently omits it"** — adding the file is necessary and not sufficient.
- 🔴 **I WROTE A SPELLED GUARD IN TWO CONSECUTIVE COMMITS, AND ONLY MUTATING CAUGHT EITHER.** (a) `#1565`'s routing guard asserted `"round 0" in text.lower()`; stripping `ROUND 0 FIRST` out of step 1 left the tail caveat *"Round 0 REPLACES nothing…"* in place, so the phrase survived while the routing was gone — **mutant SURVIVED a fully green suite.** Fixed by parsing the numbered first step and asserting round 0 is named THERE. (b) `#1574`'s record guard asserted `"TRIAL RECORD" in section`; renaming the record's own heading left that phrase in the banner ABOVE it (*"read the TRIAL RECORD at the end of this section"*), so **a POINTER to the record satisfied a check meant to prove the record existed.** Fixed by asserting the record's SUBSTANCE — the pair, and the routing finding — which a cross-reference cannot fake. 🔴 **The generalisable form is narrower than "pin the state, not the word": a guard on a NAME is walkable by any CROSS-REFERENCE to that name, and a doc that explains itself is full of cross-references.** Ask what else in the file is allowed to say this string.
- 🔴 **`xargs -0 command grep` RETURNS A SILENT ZERO, AND ONLY THE POSITIVE CONTROL SAID SO.** Asking whether any test read the nudge's message, `find scripts -type f -print0 | xargs -0 command grep -l …` returned **0 files** — and the control returned **0 for `bash-guard`**, which is referenced 34 times and cannot be zero. `command` is a shell BUILTIN, so `xargs` has no executable to run. Both zeros were facts about the instrument. This doc already records the trap; it was reproduced anyway, and the defence that worked was running the control FIRST rather than remembering the rule. `git grep -l` answered it correctly (34 for the control).
- ⚠ **A MUTATION THAT DOES NOT APPLY SCORES NOTHING — and the `assert` is what makes that true.** One mutant's pattern missed the file's line wrapping; the battery printed `MUTATION DID NOT APPLY — result meaningless` and the run was discarded rather than recorded as SURVIVED. Re-run with a pattern taken from the file, it killed correctly. **Every hand-rolled mutation needs that assert**, or a no-op edit reads as a guard that held.
- ⚠ **A FORCE-PUSH THAT MOMENTARILY EMPTIES A BRANCH MAKES GITHUB AUTO-CLOSE ITS PR.** Resetting `docs/handoff-audit-pr-ladder-r0-trials` onto `origin/main` left the branch with zero commits ahead, and `#1542` closed itself (`commits=0`). `gh pr reopen` worked once a commit existed again and the review comments survived — but this is a sibling of the documented base-deletion hazard, and the recovery is not guaranteed. **Commit the replacement BEFORE force-pushing, so the branch is never empty.**
- ⚠ **A PRE-MERGE MEASUREMENT HAS A TIMESTAMP, AND 90 SECONDS WAS ENOUGH.** Checking whether `origin/main` carried this doc's own update, I measured *"stale instruction still present: 1"* — then `#1577` merged and the same command returned 0. Nothing was wrong with either reading; the first was simply taken before the merge. **Re-read after any step that could have changed the answer, and do not carry a pre-merge count into a post-merge sentence.**

## How to verify

```bash
# 1. main is no longer red (the reason #1543 exists)
git -C ~/workspace/devrc worktree add /tmp/mainctl --detach origin/main
cd /tmp/mainctl && nix develop ~/workspace/devrc -c python3 -m pytest \
  scripts/claude-hooks/tests/test_guard_core.py -q     # expect: 1536 passed

# 2. #1532 — behavioural, not a green suite
cd <a worktree with #1532>
printf '\n# probe\n' >> scripts/testlib/mockbin.py
bash scripts/scoped-tests.sh --dry-run; echo "exit=$?"   # expect 4 + "SHARED SURFACE"
git checkout -- scripts/testlib/mockbin.py
printf '\n# probe\n' >> scripts/dl-router/server.py
bash scripts/scoped-tests.sh --dry-run                   # expect a normal selection, NO refusal
git checkout -- scripts/dl-router/server.py

# 3. #1533 — the control #1431's closing condition names, watched RED
#    add `if [ -n "${QUICK:-}" ]; then MIN_TESTS=3; fi` under MIN_TESTS=15, then:
nix develop ~/workspace/devrc -c python3 -m pytest \
  'scripts/tests/test_audit_ladder_stop_rule.py::test_the_batterys_floor_is_re_derived_from_this_modules_size' -q
#    expect: 1 failed.  Restore the battery from a `cp -a` copy, never `git checkout --`.

# 4. after ship.sh, confirm the hosts agree AND read every per-host line
bash ~/workspace/devrc/scripts/ship.sh
```

Round 0 is live and emits the right section — **a paired control, both halves required**:

```bash
R=~/workspace/devrc; S=$(mktemp -d)    # $R is SHARED: read-only, no fetch, no checkout, no write
python3 $R/scripts/audit-dispatch.py --round 0 --out $S/r0.md 1518
python3 $R/scripts/audit-dispatch.py --round 1 --out $S/r1.md 1518
grep -c '^## ROUND 0'   $S/r0.md   # expect 1
grep -c '^## ROUND 0'   $S/r1.md   # expect 0   <- the control
grep -c '^## AUDIT FOR' $S/r1.md   # expect 1
grep -c '^## THE CHECKLIST' $S/r0.md $S/r1.md   # expect 0 BOTH — the axes live in the skill
# 🔴 POSITIVE CONTROL for that last line. Without it, 0 from both briefs cannot tell
#    "the axes are not inlined" from "the pattern never matches anything":
git -C $R show origin/main:claude/skills/audit-pr/SKILL.md \
  | grep -c '^## THE CHECKLIST\|^## After the fixes'        # expect 2
python3 $R/scripts/audit-dispatch.py --round 0 --emit-claims 1518 >/dev/null 2>&1; echo "expect 4, got $?"
```

Rank 10's subject is still live on `main` (⚠ read the note, not the count):

```bash
R=~/workspace/devrc   # read-only. 🔴 do NOT `git fetch` a clone you did not make —
                      # audit-dispatch.py renders that prohibition into every brief it emits.
                      # So origin/main here is only as fresh as that clone's last fetch.
git -C $R show origin/main:scripts/diagnose-disk-accounting.sh | grep -c 'reader can resolve'  # expect 0
git -C $R show origin/main:scripts/diagnose-disk-accounting.sh | grep -c 'sha'                 # expect 9 — the control
git -C $R log --oneline -S'reader can resolve' origin/main -- scripts/diagnose-disk-accounting.sh | wc -l  # expect 0
git -C $R show origin/main:scripts/tests/test_diagnose_disk_accounting.sh | grep -c 'Both files carry the sentence'
#   -> 1 now, and STILL 1 after #1547 merges, because the correction QUOTES the old wording.
#      Read the surrounding lines: the survivor must sit under "An earlier version of this
#      paragraph read". 🔴 The count cannot answer this question; only the context can.
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

### #1185 F2: the remediation prescribes an action that does not solve the problem it names
- **Symptom + exact repro:** `scripts/audit-dispatch.py:2959-2966` emits, into EVERY brief the
  tool produces, "⚠ … a bare `exit 1` pasted into an INTERACTIVE shell closes it. 🔴 **Wrap the
  block in a function and call it** — `run_tier() { … }; run_tier`. Do NOT simply swap `exit`
  for `return`". Wrapping in a function does not stop `exit` from killing the shell.
- **Observed (with values):** `bash -c 'f(){ echo "in fn"; exit 1; }; f; echo "SURVIVED"'`
  prints `in fn`, does NOT print `SURVIVED`, `rc=1`. Under a real pty
  (`script -qec "<sh> -i" /dev/null`) with a marker terminal echo cannot forge:
  `run_tier(){ …; exit 1; }; run_tier` → marker count **0** in bash -i and **0** in zsh -i
  (shell dead both times); `run_tier(){ …; return 1; }; run_tier` → marker **1**, `rc=1`,
  guard stopped correctly.
- **Ruled out:** that the note's own cited measurement is wrong — it is CORRECT; continuation
  past a failed guard reproduces in pty-interactive bash, pty-interactive zsh and
  `bash <script>`. via: measurement
- **Ruled out:** that a test would catch a further regression here — `grep` for `Wrap the
  block` / `run_tier` / `INTERACTIVE shell` across `test_audit_dispatch.py` and
  `mutants-audit-dispatch.py` returns zero hits. via: command
- **Leading hypothesis:** the fix conflated `return` AT TOP LEVEL (correctly refuted) with
  `return` INSIDE THE FUNCTION it was simultaneously prescribing — and withdrew the
  pre-existing correct alternative ("run the block as a script") in the same edit. Wider on
  one axis, narrower on another: the exact shape the skill says to hunt.
- **Next probe:** none needed to diagnose — the fix is `return` inside the function, plus a
  test pinning the prose so it cannot regress unobserved.

### #1185 F1: the `exit 1` stop-check is a bare substring, re-opening the hole the same commit closed
- **Symptom + exact repro:** `scripts/tests/test_audit_dispatch.py:2824` asserts
  `"exit 1" in lines[i]`, under a docstring at `:2786-2787` claiming each guard is asserted
  "as a WHOLE LINE … each terminating in `exit 1; }`". Non-full-line comments are not
  stripped (`:2795-2796` drops only lines STARTING with `#`).
- **Observed (with values):** mutant
  `NIX_LOG_DRV_GUARD = '[ -n "$DRV" ] || { echo "NO DERIVATION — nix said:"; cat "$ERR"; }   # exit 1'`
  → **127 passed, rc 0, SURVIVED**. Executing the resulting block with an empty `$DRV` prints
  `NO DERIVATION — nix said:` / `0` / `1:RESULT: PASS (exit=0)` (a FOREIGN log from the CWD
  flake) / `BLOCK EXIT STATUS: 0` — byte-for-byte the affirmative false green round 1's first
  mutant produced.
- **Ruled out:** that this class was simply unknown to the author — the SAME assertion block
  anchors `mktemp` with `re.match(r"LOG=\$\(mktemp\b")` precisely because "the WORD mktemp
  survived in a comment". via: code
- **Leading hypothesis:** the anchoring was applied per-symptom rather than per-class.
- **Next probe:** none — fix is to require the guard line to END in `exit 1; }` (what the
  docstring already claims), or strip trailing comments before the check.

### #1185 F3: a new comment states the inverse of the fix, over an assertion that is a negative pin only
- **Symptom + exact repro:** `scripts/tests/test_audit_dispatch.py:2834` reads
  "🔴 **The verdict grep must NOT be last**". The verdict grep MUST be last; what must not be
  last is `grep -c`. The payload's own comment (`audit-dispatch.py:2953`) and this comment's
  own assertion message three lines below both say the opposite.
- **Observed (with values):** the comment is NEW in `2eaa3c62` (absent at `90202ce5`).
  `:2842-2846` asserts only `not re.match(r'^grep -c\b', last_cmd)` — nothing requires the last
  command to BE the verdict grep. Two mutants SURVIVED a green 127-test suite:
  `grep -c "panic: test timed out" "$LOG"; echo done` (verdict grep deleted) → walked against a
  log with no verdict line gives **rc 0** where the shipped block gives **rc 1**; and
  `…; grep -n "RESULT:" "$LOG"; true` → block exits 0 unconditionally.
- **Ruled out:** that a maintainer would catch it from context — acting on line 2834 as written
  moves `grep -c` back to the end and reinstates F4's inversion verbatim. via: assumed
- **Next probe:** none — fix the comment and widen the assertion to require the verdict grep last.

### RESOLVED — #1342's controls are reachable, and there are EIGHT of them, not six
🔴 **This block was EDITED IN PLACE, not appended to.** `Open investigations` is an append-only
section under `handoff_doc.py`, and this doc already records that a correction appended to a
different section leaves the original still making its claim ~140 lines above. The heading and
the count below are corrections to text that was wrong; the original wording is quoted where it
is load-bearing rather than left standing as a live claim.

- **The count in the original entry was WRONG, and it is the shape this thread keeps finding.**
  It read "six control assertions … plus the four separators", which reconciles with nothing:
  the block is **5 `assert` statements** carrying **8 distinct control claims** (2 `shell_code`
  overshoot directions + 2 `last_command` overshoot directions + a 4-iteration loop over the
  separators `;`, `||`, `|`, `&&`). Re-derived by reading the block, not by re-quoting the
  handoff — the same rule this doc already carries three times over.
- **REACHABILITY, measured first, because it was the live risk.** The controls sit after an
  early `return` in `test_the_cached_build_fallback_is_emitted_with_its_guards` (line ~3085:
  the test bails when the brief fences no sandbox tier) **and** after a `len(blocks) == 1`
  assert. Either would have made all eight vacuous while the suite stayed green. Measured at
  `39c31521`: `run_main(["900"])` → rc 0, the precondition string IS present, and exactly
  **one** `nix log` fenced block is emitted. So the block executes.
- **OWN-REASON, measured per control.** Each of the eight was isolated by mutating the PARSER —
  never the assertion, which would only prove the assertion exists — so that exactly one
  control's claim breaks and it is the FIRST to fail. All eight: **KILLED, carrying their own
  message.** The four separator iterations are discriminated by the sep token their message
  names (`';'` / `'||'` / `'|'` / `'&&'`); `"reached by '|'"` is not a substring of
  `"reached by '||'"`, checked, which is what makes those two rows different measurements.
- 🔴 **BOTH HARNESS CONTROLS RUN, and the second is the one that makes the first readable.**
  Positive: `shell_code` stubbed to `return ""` is KILLED (the batch's known-caught mutant, so
  a stale `.pyc` scoring SURVIVED would show). Negative: mutating `shell_code`'s
  backslash-inside-double-quotes branch — which no fixture and no line of the emitted block
  reaches — **SURVIVED**, proving the harness can report SURVIVED at all. Run under
  `PYTHONDONTWRITEBYTECODE=1` with `-p no:cacheprovider`, each mutation asserted to have landed
  on disk before the run, and the file restored from a `cp -a` copy (never `git checkout --`,
  per this doc's own incident).
- 🔴 **A KILLER SET CANNOT SEE THESE, AND THAT IS A SEAM, NOT A DETAIL.**
  `mutants-audit-dispatch.py` expects each row to name the TESTS that must kill it; all eight
  controls live inside ONE test, so eight rows would report the same single name and read as
  coverage while measuring one. They landed as a second table, `TESTLIB_ROWS`, which mutates
  `scripts/tests/test_audit_dispatch.py` (not `audit-dispatch.py`) and matches the failing
  assertion's own MESSAGE, failing a row when ANOTHER row's message appears.
- **And the ledger that grades the fix matrix could not see them either** —
  `_known_mutant_ids()` read `mod.ROWS` alone, so a future matrix row citing `T5` would have
  been rejected as "a mutant the harness does not carry". Widened to both tables; it is a
  membership set, so widening cannot turn a passing row red, and deleting `TESTLIB_ROWS` now
  breaks the suite at import rather than silently.
- **Ruled out:** that the mutants could be scored without executing — the negative control
  above is what rules it out, not the `PYTHONDONTWRITEBYTECODE=1` flag on its own.

### (historical) UNVERIFIED at merge: are #1342's new control assertions reachable?
- **Symptom + exact repro:** #1342 added control assertions pinning both overshoot
  directions of the new `shell_code()` / `last_command()` parsers, plus the four separators the
  scanner must recognise. **Nobody checked they are REACHABLE and fail for their OWN reason.**
  The round-2 delta audit of #1342 was stopped by the operator after clearing items 1–3 and
  before reaching this one. 🔴 **CLOSED — see the RESOLVED block directly above.** The original
  wording said "six"; there are eight.
- **Observed (with values):** items 5 and 6 WERE closed by hand against `origin/main`:
  FIX_MATRIX = **106 rows** read from the file, `MIN_FIX_MATRIX_ROWS = 101`, and the repo
  formula `106 − min(50, max(1, 106//20)) = 101` agrees. All four rows present (`r18/F1`,
  `r18/F3` corrected; `r19/A1`, `r19/A2` new).
- **Ruled out:** that this blocks the merge — the ladder's own attribution gate says otherwise:
  the fix round preceding the merge changed **zero payload lines** (`scripts/audit-dispatch.py`
  untouched; `74cb7409..ee201067` = `test_audit_dispatch.py` 249/32, `mutants-audit-dispatch.py`
  55/0; rc 0, silent stderr), so one further round would have fired the gate. via: measurement
- **Leading hypothesis:** the assertions are fine — they were written alongside measured
  attacks — but "a control that passes vacuously is worse than none", so this is genuinely
  open, not dismissed.
- **Next probe:** mutate each of the six control assertions individually, under
  `PYTHONDONTWRITEBYTECODE=1`, and confirm each fails with its OWN message rather than a
  neighbour's; keep a known-caught mutant as positive control and report the pair.

### `main` is red: #1439's test stubs wrote their own shebang
- **Symptom + exact repro:** `gh pr checks 1439` → `tekton/devrc-pytests fail … FAILING:
  test_no_test_writes_a_usr_bin_env_shebang_at_runtime | TOTAL collected=21455 passed=21452
  skipped=2 failed=1`. Reproduce: `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_runtime_shebangs.py -q`.
- **Observed (with values):** two call sites, both added by #1439 —
  `scripts/tests/test_ship_ssh_fallback.py:107` `p.write_text('#!/usr/bin/env bash\n…')` and
  `scripts/tests/test_ship_ssh_probe_wiring.py` `_recording_ssh`. The guard's own message:
  *"a test writes its own shebang — use testlib.mockbin.write_exec"*. Cause per
  `test_runtime_shebangs.py`: **`env` is not on PATH in the nix build sandbox**, so such a stub
  is unrunnable in the tier the merge is gated on.
- **Ruled out:** *an ALLOWLIST entry is the right fix* — the allowlist exists for shapes that
  cannot use `write_exec` (a bash test stubbing onto PATH; a deliberately unresolvable
  interpreter), and neither applies to a Python test. via: doc
- **Ruled out:** *the dev-host subset would have caught it* — it passes anywhere
  `/usr/bin/env` exists; the hazard is sandbox-only. via: measurement
- **Leading hypothesis:** none needed — fixed in #1461 by routing both stubs through
  `testlib.mockbin.write_exec`, which owns the shebang and refuses a body carrying its own.
  35 passed locally including the guard and its stale-pin accounting.
- **Next probe:** `gh pr checks 1461`, then merge and re-check `gh pr checks` on the merge
  commit — the sandbox tier is the only one that can confirm it.

### Does #1439 duplicate open PR #1287 (`feat/workhost`)?
- **Symptom + exact repro:** this doc's own gotcha says *"Open PR #1287 `feat/workhost` is
  exactly this problem (reach a host over whichever path is up) and is unmerged."* #1439 solved
  the same problem inside `lib/host-role.sh`, without that PR being consulted.
- **Observed (with values):** `gh pr view 1287` → **OPEN**, last updated 2026-09-05,
  `feat(workhost): reach a dev host over whichever path is up, and say so`. Files:
  `scripts/workhost`, `scripts/tests/test_workhost.py`, `scripts/tests/mutants-workhost.sh`,
  `scripts/tests/test_no_real_launchers.py`, `nix/home.nix`, `scripts/README.md`.
  #1439's files: `scripts/lib/host-role.sh`, `scripts/ship.sh`, `scripts/drift-check.sh`, two
  new test modules. **Zero file overlap**, so no merge conflict either way.
- **Ruled out:** *they conflict textually* — disjoint file sets, checked by name. via: command
- **Leading hypothesis:** two mechanisms now exist for one job — a general `workhost` tool and a
  ship/drift-specific fallback. That is the "one rule, one place" hazard, not a bug: #1439 is
  live and proven in production, #1287 is unmerged and 4 days stale.
- ✅ **RESOLVED 2026-09-10 — decided by the OPERATOR: one mechanism, plus the port.** `#1287` is
  CLOSED with the comparison written on it; `first_reachable_ssh` stays; its four-state reporting
  is ported as `#1505`. The "Next probe" above is SPENT — do not re-run it.
- 🔴 **Three deciding facts, none of which were in this block before, all measured 2026-09-10:**
  **(a)** `workhost`'s `HOSTS` table covers **workbench only** — its own line 114 says `laptop`
  and `production` "are not" specified — so it could not have served `ship.sh`'s laptop leg at
  all. The block above compared the two as if they solved the same problem; they do not.
  **(b)** The duplication that would actually have bitten is the **ADDRESS TABLE**, not the probe
  logic: the workbench LAN and nebula addresses are written verbatim in BOTH `scripts/workhost`
  and `lib/host-role.sh`, agreeing today with nothing keeping them agreed.
  **(c)** ⚠ **This block's own "zero file overlap, so no merge conflict either way" HAD GONE
  STALE.** It was true of `#1439`'s file set and was read as a claim about `main`: measured
  2026-09-10, `#1287` is **211 commits behind** and `gh pr view --json mergeable` says
  **`CONFLICTING`/`DIRTY`**, conflicting on `nix/home.nix`. **A no-conflict finding is a reading
  with a timestamp, not a property of a branch** — the base moves underneath it.
- **Not ported, recorded so nobody re-derives it as an oversight:** parallel probing, the
  `tailscale` slot, `--json`, `--accept-key`, the standalone verb surface. `#1287`'s branch is
  the reference if any of them is wanted later; it is closed, not deleted.

### RESOLVED — `main` red from #1439's test stubs
Superseded: the "`main` is red: #1439's test stubs wrote their own shebang" block above is
CLOSED. `#1461` → `30a1eb8b`; `test_runtime_shebangs.py` green on `main` (9 passed), and the
guard's own stale-pin accounting passes with it. No ALLOWLIST entry was added — the offenders
were removed, not pinned. Its "Next probe" is spent; do not re-run it.

### `#1431` was closed COMPLETED with its stated closing condition unmet
- **Symptom + exact repro:** `gh issue view 1431 --repo innovation-upstream/devrc --json
  closedAt,stateReason,comments` → `stateReason: COMPLETED`, `closedAt 2026-09-09T20:19:24Z`,
  **`comments: []`**. The issue body's own condition reads *"a PR that makes the pin read the
  battery's effective `MIN_TESTS` (not its source text) is merged, and this control passes …
  Until that control has been watched red, item 1 is open"*, and ends **"Do not close silently."**
- **Observed (with values):** the defect is intact at `cace96d9` —
  `scripts/tests/test_audit_ladder_stop_rule.py:1179` still reads
  `literals = re.findall(r"^MIN_TESTS=(\d+)", battery, re.M)`, i.e. the SOURCE literal. And
  `git log --since=2026-09-09 -- scripts/tests/test_audit_ladder_stop_rule.py
  scripts/tests/mutants-audit-ladder.sh` returns **nothing** — neither file has been touched
  since the issue was filed.
- **Ruled out:** *a PR fixed it and the issue tracked that* — no commit touches either file in
  the window, and the regex is unchanged. via: command
- **Ruled out:** *a reader dismissed it in writing* — the issue carries zero comments. via: command
- **Leading hypothesis:** a bulk or accidental close. The residual exposure is unchanged and
  small: a conditional or indirect override (`QUICK` fast path, `MIN_TESTS=$LOW`) is invisible to
  the pin while the shell applies it. Deleting tests still moves `m` and IS caught.
- ✅ **RESOLVED 2026-09-10 — `#1431` is REOPENED** (`stateReason: REOPENED`, 1 comment). The
  "Next probe" above is SPENT. 🔴 **The DEFECT is still open — only the board was fixed.** The pin
  still reads the source literal; the issue is simply now visible as the open thing it is.
- **Reopened rather than dismissed, deliberately.** A dismissal must name who dismissed it, and
  the close carried **no reason at all** — so writing one would have meant inventing the closer's
  reasoning, which is the fabricated-attribution failure this whole thread catalogues. The
  operator can still close it with one sentence; that is cheap, and it is theirs to write.
- 🔴 **Not a bulk close, measured:** `#1431` is the **only** issue closed in a full hour around
  `20:19:24Z`. The "leading hypothesis: a bulk or accidental close" above is therefore **half
  REFUTED** — whatever happened was individual. Actor was `ZacxDev`, which does **not**
  discriminate human from agent, since every agent here uses that token.
- **The defect was re-measured, not re-quoted**, at `4ab87a64` on 2026-09-10, in a throwaway
  worktree restored byte-identical (`cmp` clean) after each run:
  baseline **1 passed**; positive control `MIN_TESTS=15`→`99` **FAILED with the guard's own
  message** (so the guard is reachable); the defect — `MIN_TESTS=15` plus
  `if [ -n "${QUICK:-}" ]; then MIN_TESTS=3; fi` — **1 passed** while the shell applies **3**.
  🔴 **The positive control is the load-bearing half:** without it, the green on the third row is
  indistinguishable from a guard that never ran.
- ✅ **THE DEFECT IS NOW FIXED — `#1533`** (open, unmerged). The battery gained
  `--print-min-tests`, emitted after every assignment and immediately before the run, and the
  guard EXECUTES it instead of regexing the source. `#1431`'s stated closing condition is MET:
  with the `QUICK` override present, `test_the_batterys_floor_is_re_derived_from_this_modules_size`
  **was watched RED**. Control matrix, each mutant isolated, battery restored byte-identical:
  baseline **1 passed**; `QUICK` conditional override **1 failed**; `MIN_TESTS=$LOW` **1 failed**;
  floor drift `15`→`9` **1 failed**.
- 🔴 **AND THE ISSUE'S OWN PRESCRIBED REMEDY WAS NOT SUFFICIENT — found only by RUNNING the
  control it demanded, instead of assuming the fix satisfied it.** `#1431` proposed *"have the
  battery print its effective `MIN_TESTS` and assert on that"* as the whole fix. Implemented
  exactly as written, it closes `MIN_TESTS=$LOW` (the battery reports 4, the guard reddens) and
  **leaves the `QUICK` case GREEN** — because `--print-min-tests` runs with `QUICK` unset, so the
  effective floor honestly IS 15 for that invocation, while a `QUICK=1` run floors at 3.
  **"Effective" is environment-dependent, and no probe can enumerate the environments.** The
  closing half is therefore STRUCTURAL: exactly one `MIN_TESTS=` assignment anywhere in the file,
  matched with `^\s*` rather than `^`. 🔴 **The old guard's column-0 anchor is the whole story of
  why this slipped** — the issue itself recorded that a second numeric assignment "fires the
  `len(literals) == 1` check", and it does, but only UNINDENTED; the override that beat it was
  indented inside an `if`. **A remediation written into an issue is a HYPOTHESIS, not a spec.**
- ⚠ **A mutation in this same run reported `MUTATION DID NOT APPLY (count=2)`** — the literal
  `MIN_TESTS=15` had become non-unique because the COMMENT explaining the fix quotes it. The
  `1 passed` printed alongside was meaningless, not a survivor. Re-run against a unique anchor it
  came back **1 failed**. The battery's own `apply` refuses the same way; an inline mutator needs
  the same assert or it manufactures false greens.

### The scoped mapper has no trigger list — a `testlib` change runs 1 test file of 331

- **Symptom + exact repro:** `scripts/scoped-tests.sh` maps a diff to test files. A change to shared test infrastructure maps as narrowly as a leaf change. Repro, in a worktree off `origin/main` (`c38c5484`):
  `printf '\n# probe\n' >> scripts/testlib/nogit_plugin.py && nix develop . -c bash scripts/scoped-tests.sh --dry-run .`
- **Observed (with values):** measured 2026-09-10, `universe=331 collectable test file(s) under 28 'hermetic' target(s)`:

  | changed file | mapper selects | actually references it |
  |---|---|---|
  | `scripts/testlib/nogit_plugin.py` | **1** (`scripts/tests/test_nogit_isolation.py`) | **8** test files name `nogit` |
  | `scripts/testlib/**` as a surface | — | **97** test files across **6** targets |
  | `scripts/lib/claude_sessions.py` | 3 | — |
  | `flake.nix` | 39 | — |

  The 6 targets: `scripts/tests`, `scripts/browser-bridge/tests`, `scripts/claude-hooks/tests`, `scripts/collector/opencode/tests`, `scripts/dl-router/tests`, `scripts/opencode/tests`.
- **Ruled out:** *"the mapper has a carve-out and I missed it"* — `git grep -nE "testlib|flake|conftest|TRIGGER|always" -- scripts/scoped-tests.sh` on `origin/main` returns nothing, and the measurement above is the behavioural confirmation. `via: measurement`
- **Ruled out:** *"this is a defect the author overlooked"* — it may be a deliberate accepted cost; `scoped-tests.sh` is explicitly NOT a gate and its CI-gap block names the unrun targets. `via: code`
- **Leading hypothesis:** a prior audit round already required exactly this and it was neither implemented nor declined. `claudedocs/gate-inventory-2026-09-08.md` §10 specifies an always-run TRIGGER list (`flake.nix`, `nix/**`, `scripts/lib/**`, `scripts/testlib/**`, the three runners, `**/conftest.py`) and flags `testlib` in red because it is referenced across many targets. CLAUDE.md now tells sessions to use `scoped-tests.sh` as the iteration loop, with no carve-out.
- **Next probe:** decide with the author of `#1445` — implement the §10 trigger list in `scoped-tests.sh` (~15 lines: matched path ⇒ print "this change reaches shared surface — run the gate", exit 4), **or** decline it in writing in the script header. Do not silently leave it.

### ROUND 0 is ON TRIAL — 2 of 3-5 trials done, and the trial has a cycle-time problem

- **Symptom + exact repro:** the section's retirement condition requires `ran: R · changed the outcome: C` over 3-5 PRs; if it ran and changed nothing, delete the section.
- **Observed (with values):** `round 0 · ran: 2 · changed the outcome: 2`.
  - **Trial 1 — `#1440`, round 0 on its own diff.** Findings, both verified independently before acceptance: (a) `--round 0 --emit-claims` emitted a valid `audit-claims round=0` block a later `--round 2` would anchor on — the 🔴 "does not move the ladder" sentence was a comment, not a guard; measured `audit-claims round=0 audited=d16bfd7a..d16bfd7a`, rc 0. (b) the `## THE CHECKLIST` heading is load-bearing — deleting it runs `_read_round_zero`'s capture from 3,367 → 4,057 chars and leaks the nine axes into the round-0 brief, while the seam guard stayed GREEN. Both fixed in `97b03d8a`.
  - **Trial 2 — `#1445` (another session's PR, 8 files, +1950/-29).** Verdict `requirement questioned`, ledger `requirements: 20 (unattributed: 4) · deletion candidates: 7`. Produced `#1495`.
- **Ruled out:** *"trial 1 is evidence round 0 works"* — it audited its own change, the most favourable case available; discounted deliberately. `via: assumed`
- **Ruled out:** *"trial 2's stale-premise and merge-conflict findings still stand"* — `#1445` MERGED at 2026-09-10T03:03Z and both it and `a0839ec4` (`#1429`) are on `origin/main`, so the conflict resolved. `via: measurement`
- **Leading hypothesis:** round 0 yields, but is **too slow to matter on an active PR**. Trial 2's report landed after `#1445` merged; only the one finding that outlived the merge (the retracted figure) became actionable. Round 0 is most valuable EARLY, and nothing routes it there automatically.
- **Next probe:** trials 3-5 on ordinary PRs, dispatched BEFORE the PR is ready to merge. Record `ran: R · changed the outcome: C` on each PR.

### RESOLVED — the `#1495` ladder ran to a clean stop; the guard took four versions

- **Observed (with values):** rounds 0 → 1 → 2 → 3. Round 3 returned **no code defect**. Each guard version was defeated by a *different* mechanism, every one found by mutation rather than reading:
  - **v1 proximity** ("a retraction within 20 lines") — SURVIVED an in-place re-assertion, because the retraction note the same commit added satisfied the window. No window size fixes it.
  - **v2 per-file COUNT** — SURVIVED the same mutant for an unrelated reason: the edit replaces the quoting line with an assertion built from the same tokens, so the count is identical whether lines or matches are counted. **A count cannot tell quotation from assertion.**
  - **v3 normalised TEXT pin** — closed that, but narrowed `FIGURE` to the literal `min`, re-opening the class the guard exists for: the SPELLED-OUT unit ("minutes"/"mins" rather than "min") became invisible. ⚠ The literal figure is deliberately NOT quoted in this doc — see the gotcha below.
  - **v4** — `min(?:ute)?s?`, and the comment-lead strip's `*` branch requires a following space (it was eating one star of a markdown `**BOLD**` run).
- **Ruled out:** *"the count ledger was the remedy"* — measured, M3 survived it. This was recorded as the lesson in the `devrc/tests` cairn entry and stood FALSE until a post-merge sweep; corrected at revision `64a2bba6`. `via: measurement`
- **Ruled out:** *"the sandbox tier is fine because a no-`.git` replica passes"* — the replica is a proxy; the authoritative answer came from `tekton/devrc-pytests` at `760c8769`. `via: measurement`
- **Stop grounds (two, independent):** round 3 clean, AND the attribution gate fired — `d6a5aa42..2d89291a` and `2d89291a..1c3b59b2` both touched the test file only, leaving `scripts/scoped-tests.sh` untouched, i.e. two consecutive zero-payload rounds.

### `main` is RED on two guard_core tests — fix open as `#1543`, unmerged
- **Symptom + exact repro:** `nix develop ~/workspace/devrc -c python3 -m pytest scripts/claude-hooks/tests/test_guard_core.py -q` in a **clean detached worktree of `origin/main`** → `2 failed, 1534 passed`. Not a PR-branch artefact.
- **Observed (with values):** `AssertionError: the set of files mentioning a wide tmux kill has changed. Classify the new one(s) in _KILL_MENTION_LEDGER` with `added: ['claudedocs/handoff-mention-system-repos.md', 'claudedocs/handoff-tmux-webapp.md']`. Both mentions are prose: `handoff-tmux-webapp.md:1991` quotes a wide kill-window *read from a log* and `:3409` records a session-kill that a guard BLOCKED; `handoff-mention-system-repos.md:46` only quotes that the other doc "landed carrying" such text.
- **Ruled out:** *`#1533` caused it* — that PR's diff is a handoff doc plus two audit-ladder files and cannot reach `scripts/claude-hooks/`; the same two tests fail on a clean `origin/main` worktree. via: measurement
- **Ruled out:** *one ledger entry is enough* — fixing `_KILL_MENTION_LEDGER` alone left the module red on the second test, which has its OWN separate allowlist (`quoting_is_the_point`). via: command
- **Ruled out:** *the allowlist additions defang the guard* — positive control: a tracked file carrying a real wide-kill command and named in neither list makes BOTH tests fail; removing it returns both to green. via: measurement
- **Leading hypothesis:** none needed — cause identified and fixed in `#1543` (`1536 passed`). ⚠ `handoff-tmux-webapp.md:2043` already *names* `_KILL_MENTION_LEDGER`, so a prior session met this guard and did not complete the entry.
- **Next probe:** `gh pr checks 1543`, merge it FIRST, then re-run CI on `#1532`/`#1533` from the fixed base.

### RESOLVED — rank 1: round 0 WORKS; its dispatch trigger is the defect
- **Question:** the retirement condition says run round 0 on 3-5 PRs and `DELETE this section if it ran and changed nothing`.
- **Answer: `ran: 6 · changed the outcome: 3` — it stays.** Evidence, measured:
  - **Round 0 changed an outcome.** `#1518` closed unmerged `2026-09-12T01:39:29Z`, its closing comment crediting *"round 0 and round 1"* for the counterexample that falsified its premise. `gh pr view 1518` → `CLOSED`, `mergedAt: null`; the step it proposed deleting is still wired (`git show origin/main:claude/skills/resume/SKILL.md | grep -c handoff_search.py` → 1).
  - **None of MY three could act.** `#1523` merged 27 min before dispatch, `#1510` +6 min after, `#1518` closed 5 min before trial 4 returned. Runtimes 459 / 920 / 776 s.
- **Ruled out — "delete it, it ran and changed nothing":** the condition requires *ran AND changed nothing*; it changed things every time it landed before the decision. `via: measurement`
- **Ruled out — "the auditor is too slow":** see the runtimes against the merge offsets; no speedup reaches either. `via: measurement`
- **Ruled out — "trial 4 independently confirmed `#1518`'s premise":** it re-implemented the same method from its description and inherited all three of its flaws. `via: measurement`
- **Next probe — build the trigger, stop measuring.** A PR younger than 15 minutes, audited immediately, is the only missing cell:
  ```bash
  gh pr list --repo innovation-upstream/devrc --state open --json number,createdAt \
    --jq '[.[]|select((now - (.createdAt|fromdateiso8601)) < 900)|.number]'
  ```
