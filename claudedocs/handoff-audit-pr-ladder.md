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
- Branch: `main`. Nothing of this work is uncommitted.
- **Eight PRs, all MERGED and verified by content in `origin/main`** (never by ancestry —
  a squash merge makes the branch head permanently a non-ancestor):
  - `#900` attribution gate · `#922` two worktree hazards · `#933` the two instruments
  - `#958` → squash `1b2117b6` — `scripts/audit-dispatch.py`, the brief assembler
  - `#979` → `8bab93b1` — espanso `:acq` split, rescued off the workbench's `main`
  - `#993` → `c7d70a40` — the ladder writeup in `round-ladder-evidence.md`
  - `#999` → `22e2830a` — espanso exact-trigger preference
  - `#1001` → `289865af` — round-13 fixes (`repo_unknown_reason`, runnable cross-repo recipe)
- **Deployed AND verified at the consumer**, not just the deploy: `#999` needed
  `systemctl --user restart keylog.service` on both hosts (workbench PID 2851719→2937124,
  laptop 1718672→1721180), then the original symptom was re-run against the deployed
  module `/nix/store/jawj46mw…-hm_keylog`: `acq` and `alo` both moved `None` → their own
  snippet, every other term unchanged.
- **Hosts CONVERGED 2026-08-28 22:37** — `ship.sh` fast-forwarded both from `1c0db104` to
  `7d3aec1a` and verified them equal; `2 hosts compared, both at 7d3aec1a`. 🔴 The workbench
  leg still printed `DIRTY AND IN THE ARTIFACT` and **that is not stale** — see the
  `discord-embed-ext` investigation below; the workbench generation is `origin/main` plus
  another session's uncommitted extension.

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
1. 🔴 **Zach: answer the stalled opencode session** (repo: `devrc`; session
   `ses_fbe5f77a2ffeaJr0G0S7i4lUKa`). It has been waiting since 21:08 on *"Reload the
   extension in Brave"*, and **`v0.2.3` is already what Brave loads on the workbench.**
   Give it the observer finding above — it is iterating on CSS and the bug is its own
   `disconnect()`/`if (found > 0)` reconnect. **Closing condition:** the WIP is committed or
   PR'd and `git -C $DEVRC status -s scripts/discord-embed-ext/` is empty; checked by
   whoever runs `ship.sh` next, since the workbench leg prints `DIRTY AND IN THE ARTIFACT`
   until then. Nothing here should commit another session's mid-iteration work for it.
2. **Land the observer fix + its first test** (repo: `devrc`,
   `scripts/discord-embed-ext/`). Reconnect unconditionally, and add the observer-lifecycle
   test the suite has never had — which needs `FakeMutationObserver` to record connection
   state and `FakeElement` to set `nodeType = 1`, or the test passes vacuously. Repro script
   + controls: see the closed investigation above. **Do this only after rank 1** — the file
   is another session's live working copy.
3. ✅ **DONE — the config/remote worktree surface is in `claude/RULES.md`** as `#1009` (the
   sixth surface, plus an "an mtime cannot NAME a writer" clause on the find-the-WRITER
   rule, plus the `worktree-config` archive anchor). It needed a ceiling bump: floor-relative
   slack was **4 B**, the eviction sweep was re-run by the ledger's own documented method and
   returned **0 B** for the third consecutive time, and the ledger had already predicted "the
   next NEW rule pays ceiling". `MAX_BYTES` 41,400 → 42,450, slack held at precedent.
4. ✅ **ANSWERED — `date` needs NO action, and that is measured, not assumed.** The condition
   this doc set was "only if the keylog shows it is a term actually typed". It is not: over
   the observed search stream, **56 term rows and `date` appears ZERO times**, with `ask`
   visible 4× in the same output as the positive control — so the zero is a measurement and
   not an empty harness. `date` matching `:eos`/`:roo` at all is a spurious SUBSTRING hit
   (`date` ⊂ "upd**ate**"); it costs nothing because nobody searches it.

### `main` WAS RED REPO-WIDE — three independent causes, all landed in `#1023`
Merged as `8e33bf1d`, verified by content. `#1015`/`#1021`/`#1022` were closed as
superseded: **none of them could go green alone**, because the gate runs the whole suite,
so each red-tested the others' bug. That deadlock is the reason they were combined.
1. **espanso `ask`** — the 08-28 `:acq`/`:dacq` split cleaned the label but left `"ask"` in
   BOTH snippets' `search_terms`, so the term named neither and `#999`'s tie-break correctly
   declined. The comment above those lines already said ":acq owns ask/clarify/questions
   alone" — the comment was right and the data contradicted it.
2. **opencode pin** — `flake.lock` moved the binary 1.18.18 → 1.18.21 under an unchanged
   config, which is exactly what `test_opencode_engine.py` exists to catch.
3. **the store-api load flake** — a 15 s localhost read timing out on SCHEDULING.

🔴 **CORRECTIONS to `#1023`'s own commit messages, recorded here because that history is
merged and will not be rewritten.** All three were caught by audit, and each is a claim I
made that the tree did not support:
- **The espanso trade-off is 9 lost multi-word queries, not 8.** `ask agent` also stops
  reaching `:dacq` — it got there only via the label word `subagent`. `--diff-config` cannot
  see it: its probe universe forms two-token pairs WITHIN ONE SNIPPET, and `ask` belongs to
  `:acq`'s word set, so the pair never exists to be tested. **The tool was quoted faithfully
  and the tool is narrower than the sentence** — none of the 9 has ever been typed, which is
  the claim that actually matters and which held.
- **"56 recovered fires" measures ATTRIBUTION, not intent.** Those fires were unattributable
  by construction, so the share that *meant* `:dacq` is unmeasurable. `ask` no longer lists
  `:dacq` at all (2 picker rows → 1); `:dacq` keeps all 8 of its unique routes. A legitimate,
  tool-sanctioned remedy — but "recovered" should not be read as pure gain.
- **"all 7 sites" / "NINE settimeout sites" were both miscounts** (6 and 8 respectively). The
  second came from counting `grep` OUTPUT LINES, one of which was a docstring mentioning the
  name. A grep counts mentions; an AST walk counts calls.
- ⚠ Three miscounted self-reports in one ladder, the last inside the guard written to stop
  them. **Re-derive every count from the tree at the moment you quote it.**

### Still open
- **`#1005`** (this doc) and **`#1009`** (the RULES worktree-config surface) — both were only
  ever blocked on `main`; rebased onto the green `main`.
- **`#1033`** — the round-1 audit follow-ups, plus the round-2 findings on top.
- ✅ **RESOLVED, and it was my instrument, not the environment:** the "CI-only" opencode
  failure. It reproduces locally in one run. I had been running `test_opencode_config.py`;
  the failing test is in `test_opencode_**engine**.py`, and my follow-up search returned a
  false zero because `xargs -0 command grep` has no executable to run — `command` is a shell
  builtin. **Two instrument errors stacked into a confident "unexplained".**
- **NOT FILED, and deliberately named as such:** the Tekton capacity problem behind the
  flake. It has no closing condition anyone can check yet, so it is not being minted as a
  work object — see the object-leak rule.

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

## How to verify
```bash
# --- the two closed investigations (2026-08-28) ---
# the stray remote is gone and has NOT come back
git -C $DEVRC remote -v | grep localverify || echo "localverify: gone"
# who owns the discord WIP — the answer is in the OTHER runtime's log, not a transcript
grep -a 'embed_enlarge' ~/.local/share/opencode/log/opencode.log | tail -3
# the observer defect, with its control (expect: WIP connected=false / main connected=true)
git -C $DEVRC show origin/main:scripts/discord-embed-ext/extension/embed_enlarge.js > /tmp/dee_main.js
node claudedocs/repro-discord-embed-observer.mjs \
  $DEVRC/scripts/discord-embed-ext/extension/embed_enlarge.js "WIP"
node claudedocs/repro-discord-embed-observer.mjs /tmp/dee_main.js "CONTROL origin/main"
# what Brave actually loads (0.2.3 = the uncommitted WIP; 0.1.0 = origin/main)
grep -o '"version"[^,]*' ~/.local/share/discord-embed-ext/manifest.json

# --- earlier work in this thread ---
# every PR landed by CONTENT, never ancestry (squash merges break ancestry forever)
git -C ~/workspace/devrc fetch origin main
git -C ~/workspace/devrc show origin/main:scripts/audit-dispatch.py | grep -c 'refs/audit/'   # 7
git -C ~/workspace/devrc show origin/main:scripts/collector/keylog/espanso_detect.py | grep -c '_names_trigger'  # 2

# the espanso fix is live in the RUNNING collector, not merely deployed
systemctl --user show keylog.service -p MainPID -p SubState
python3 - <<'PY'
import sys, yaml
D = "/nix/store/jawj46mwcz9xd5ly4ixja5kdr9wvsmf6-hm_keylog"   # re-resolve after any switch
sys.path.insert(0, D)
import espanso_triggers as ET
from espanso_detect import EspansoDetector
d = EspansoDetector(ET.load_triggers(yaml.safe_load(open("/home/zach/.config/espanso/match/base.yml")), {"search_shortcut": "CTRL+SPACE"}))
for t in ("acq", "alo"):
    print(t, "->", d._attribute(t))    # expect :acq and :alo, NOT None
PY
```
