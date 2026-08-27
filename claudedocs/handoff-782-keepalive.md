# Handoff: devrc push keepalive (#782) — 2026-08-27

> **STATUS — updated 2026-08-27 18:50Z (second session).** #782 closed and live. **#908 MERGED**
> (`648f08c2`), #905 closed. **The only item left is step 2: arming the gate** — deliberately
> deferred by Zach with its cost measured, not forgotten.
>
> 🔴 **If you read an earlier revision of this doc, two of its claims were wrong.** It said #908 was
> *"awaiting the required approving review"* (no review is required — only two Tekton checks), and it
> named **generation 572** as the keepalive's rollback target (generations have since churned to
> **584**; roll back by content, not by number). Both corrected below.
>
> 🔴 **And step 2, as this doc originally wrote it, was DANGEROUS until `648f08c2` landed** — a
> relative `githooks/install.sh` would have corrupted `~/.gitconfig` and broken git in every repo on
> the box. See the CDPATH gotcha. It is safe now; invoke by absolute path.

_No `clawgate-task:` front matter, deliberately._ The resolver returned **exit 6** with one link —
**#306, role=`read`** — which is the *previous* session's clawgate task-threads card, fetched by
`/resume`'s reconciler, not worked here. This session's work was **devrc issue #782**, a GitHub
issue with no clawgate card. Reading a task is not doing its work, so nothing is recorded.

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
Close devrc **#782** — `git push` returning **141 (SIGPIPE)** with the branch never created, after
the `pre-push` test gate runs. Reproduce it, fix it, ship it, and make it live.

## State now

- **#782 is CLOSED — reproduced, fixed, merged, deployed, and verified against the real failing
  path.** Not "deployed"; verified.
- **Mechanism, measured 2026-08-26 against real github.com, twice independently:**
  github.com closes an **idle `git-receive-pack` session after ~360 s** (361 s in both runs; the
  clean one returned `rc=255` + `Connection to github.com closed by remote host.`). git opens **and
  negotiates** the connection **before** running `pre-push` — measured with a `GIT_SSH_COMMAND`
  stamp, not inferred from interleaved output: `ssh-launch 04:12:04Z` then `hook START 04:12:05Z`.
  So the connection idles for the hook's whole runtime.
- **It is NOT flaky. It is a hard threshold**, and it fires more often the longer the suite grows.
  It reads as a network error because the hook prints `✅ devrc test suite passed.` *after* the
  connection has already died.
- **Fix: PR #887, MERGED `1d543186`** — `core.sshCommand = "ssh -o ServerAliveInterval=30 -o
  ServerAliveCountMax=6"` in `nix/programs/git/default.nix`, plus the two `GIT_SSH_COMMAND` exports
  that would otherwise bypass it (`scripts/claim-work.sh`, `scripts/resume-state.sh`), plus a
  ledger test over every such export.
- **DEPLOYED AND LIVE** — first switched 2026-08-27 at generation **573**.
  `git config --global --get core.sshCommand` returns the value.
  🔴 **DO NOT use the rollback target this line used to name (generation 572).** Generations churn
  fast here — 573 when this doc was written, **575** four hours later, **584** by 18:50Z the same
  day, all from other sessions. Rolling back to a hardcoded number now discards unrelated deploys.
  **Re-verify by CONTENT instead**, which is stable and was re-confirmed at gen 584:
  `readlink -f ~/.config/git/config` → `…1yxm38gq0fnbp87d54438mmksgc3jsg3-hm_gitconfig`.
- **Follow-on defect found and filed: issue #905** — `githooks/install.sh` cannot install the hooks
  **at all** on a home-manager host. ✅ **CLOSED. Fix MERGED as `648f08c2` (PR #908), 2026-08-27
  18:41Z**, after a blind pre-merge audit found a deploy-blocking defect in it (see the CDPATH
  gotcha below). CI at the merged tip: nodetests 1292/1292, pytests 17250 passed / 0 failed.
  ⚠ **This doc previously said #908 was "awaiting the required approving review". There is no such
  requirement** — `main`'s protection requires only `tekton/devrc-nodetests` and
  `tekton/devrc-pytests` (`required_pull_request_reviews` is **null**). An empty `reviewDecision`
  means no review *exists*, not that one is *demanded*; reading it as a blocker cost a cycle.
- ⚠ **#908 merged ≠ the gate armed. STILL TRUE, and it is now the only thing left.** It fixes the
  installer; it does not run it. Re-verified 2026-08-27 18:50Z: `core.hooksPath` **unset**,
  `~/.gitconfig` **absent**, the blocking pre-push gate has never run on this host.

### Verified vs merely deployed
| claim | evidence |
|---|---|
| **#782 fixed on this host** | 🔴 the row that closes it. Same harness that produced the failure: 420 s hook, **`GIT_SSH_COMMAND` unset** so the keepalive can only come from the deployed config → `push_rc=0`, `remote_sha == local_sha`, `BRANCH_CREATED`. Previously `rc=141` / `BRANCH_ABSENT` |
| the deploy is real | `nix eval` predicted gitconfig derivation `1yxm38gq…`; after the switch `readlink -f ~/.config/git/config` **is** `1yxm38gq…` (was `nxa6x4jl…`) |
| no version skew introduced | `clawgatectl` still `0.8.1` after the switch, matching the live server |
| #887 merged by CONTENT | `git show origin/main:nix/programs/git/default.nix` carries `core.sshCommand`; both exports carry the option. **A squash merge is never an ancestor — never verify by ancestry** |
| #908 does not mask home-manager settings | measured outside any repo with both global files present: `rebase.autoStash=false`, `merge.autoStash=false`, `diff.algorithm` all survive. **git reads BOTH global files**; only `--global --list` narrows its view |
| #908's `--uninstall` respects provenance | operator-created empty `~/.gitconfig` **survives**; installer-created one is still **removed** (both arms — the second is what stops "survives" being satisfied by an uninstall that deletes nothing) |

### NOT verified — stated plainly
- **The laptop was never touched.** Everything above is the workbench only. `nix/programs/git` is
  shared, so the laptop gets the keepalive on its next `home-manager switch` — unverified.
- **Never measured that the real devrc suite exceeds 361 s.** The reproduction used a `sleep` hook
  deliberately (isolates duration, finds the threshold, avoids running the suite that travelled the
  GIT_DIR-incident path against a clone that must keep `origin`). That is the gap between
  "mechanism reproduced" and "this is precisely what happened on those two pushes".
- **361 s is a GitHub-side value measured twice on one day**, not a documented constant. The fix is
  pinned against it *with margin*, not tuned to it. Keepalive validated to 1367 s, not indefinitely.

## Open investigations — live diagnosis state

### `scripts/present/measure.py` can report "no pre-push gate installed" while one IS armed
- **Symptom:** a latent misreport, not yet observed in the wild. Found during #908's audit.
- **Observed (values):** `m_hook_gate_install` (~`scripts/present/measure.py:1089`) reads
  `git config --local --get core.hooksPath; git config --global --get core.hooksPath`. Measured on
  git 2.55.0: when **both** `~/.gitconfig` and `~/.config/git/config` exist, `git config --global
  --get <key>` reads **`~/.gitconfig` only** — the XDG file's keys stay *in effect* but vanish from
  `--global` scope reads. Confirmed live: with both present, `--global --list` printed only
  `core.hookspath=/some/hooks`.
- **Ruled out:** that it breaks today — `core.hooksPath` is exactly the key #908's installer writes
  into `~/.gitconfig`, so the reader finds it. Also ruled out for `scripts/run-tests.sh:2183`, which
  **unions** the `--show-origin` result with hardcoded `~/.gitconfig` + XDG paths, so its protected
  set can only grow.
- **Leading hypothesis:** the break needs `core.hooksPath` to land in the **XDG** file *and*
  `~/.gitconfig` to appear later. Reachable if home-manager ever declares `core.hooksPath`.
- **Next probe:** decide whether `measure.py` should read effective config
  (`git config --get core.hooksPath`, no `--global`) instead of scope-qualified. One-line change;
  the question is whether the measure *wants* scope or effect.
- ✅ **RE-CHECKED against `origin/main` 2026-08-27 18:50Z — still live, still unfixed, and the
  question above is ANSWERED.** The reader is now at **`scripts/present/measure.py:1284`** (the
  `:1089` above had drifted; two commits touched the file on 2026-08-26, `e9c2adad` and `30acd174`,
  both *before* this doc, so nothing landed under this item). The answer: **the function's own
  docstring at `:1264` already says `git config --get core.hooksPath` "is what answers this"** —
  i.e. the unqualified form — while the body at `:1284` runs `--local`/`--global`. That is a
  docstring claiming something the code does not do, so the intended semantics are *effect*, not
  scope, and the one-line change is simply making the body match the sentence above it.
  ⚠ **#908 merging did NOT close this** — the merged installer still writes `core.hooksPath` into
  `~/.gitconfig`, which is exactly the file `--global` still reads, so the "ruled out for today"
  line remains true and the latent break remains latent.

## Next steps (ranked)
🔴 Numbering is stable — `claim-work --slug-for <this doc> <rank>` is the lock; re-ranking
re-points every live claim.

🔴 **Ranks are NOT renumbered when an item completes** — a claim slug is `<doc>-<rank>`, so
renumbering silently re-points every live claim at a different item. A finished item is struck
through in place and the number is retired with it.

1. ~~**Review and merge devrc #908.**~~ ✅ **DONE 2026-08-27 18:41Z — merged as `648f08c2`,
   branch deleted, #905 auto-closed.** Verified by CONTENT (`git show origin/main:githooks/install.sh`
   carries `CDPATH= cd -P` and `_reject_bad_dir`), never by ancestry — **a squash merge is never an
   ancestor of its base**. Shipped with two extra commits from the audit ladder; see the CDPATH
   gotcha. Rank retired; do not reuse `782-keepalive-1`.
2. 🔴 **NOW THE TOP ITEM. Run `githooks/install.sh` for the first time on this host.** The installer
   is finally both *installable* (#908) and *safe to invoke* (the CDPATH fix). This is the real
   threshold, not the merge: success arms a **blocking** pre-push gate (`TESTS_ON_PUSH=on` inside
   devrc) that has never run here. #782's keepalive is deployed, so the pairing is right.
   Watch that first push; verify with `git ls-remote`, never the wrapper's rc.
   ⚠ **PRICE IT BEFORE ARMING — this reaches other people's sessions, not just yours.**
   `core.hooksPath` is set **GLOBALLY**, for every repo and every concurrent session on the box.
   🔴 **CORRECTION — an earlier revision of this line said "10m37s", and that number was WRONG AND
   WRONGLY DERIVED.** It came from a **serial** `pytest scripts/tests/` run (637s) quoted as if it
   were the gate's cost; the gate does not run serially, it runs `-n 4 --dist loadfile`. Right
   ballpark, invalid derivation — the kind of asserted measurement that stops the next reader
   checking. The authoritative figure is the census `scripts/run-tests.sh` prints every run:
   **527s (~8.8 min) over 31 targets**, and it is concentrated — `scripts/tests` 209s (40%),
   `browser-bridge` 143s (27%), `dl-router` 99s (19%), the other 28 targets ~76s **combined**.
   **~20 devrc worktrees** on this host satisfy `tests-on-push.sh`'s applicability gate, and ~15
   agent sessions run concurrently.
   🔴 **The gate is FLOOR-BOUND, not core-bound — do not try to fix it with more workers.**
   Measured 2026-08-27: `--dist loadfile` pins a file to ONE worker (deliberately; several suites
   share module state), so a target cannot finish faster than its slowest FILE.
   `test_subsystem_store_api.py` alone is **~178s** and `test_run_tests_floors.py` alone is
   **98.7s for 20 tests**, against a whole-target time of **237s at `-n 4`** — i.e. `scripts/tests`
   is already within ~1.3x of its own floor. The host has 24 cores but sits at load ~15.9.
   Two distinct causes, needing different fixes: `test_run_tests_floors.py` **spawns the real
   `run-tests.sh` 20 times** (~5s each), while the `store_api` slowloris / half-open / dripped-body
   tests **wait on hardcoded wall-clock deadlines** — the `25.01s / 15.02s / 10.01s / 5.51s / 4.00s`
   quantisation in `--durations` is the fingerprint, and none of those deadlines is env-tunable
   today. Repos carrying a **repo-local** `core.hooksPath` are unaffected (measured:
   `homelab-talos` + 17 of its worktrees, `kubeclaw{,-cloud,-embed}`, `promptver`) — a local value
   overrides the global one entirely. **Zach deferred this deliberately on 2026-08-27 with those
   numbers in view; it is a decision awaiting him, not an oversight.**
   🔴 Invoke by **absolute** path (`$HOME/workspace/devrc/githooks/install.sh`).
3. **Fix or close the `measure.py` latent misreport** (see Open investigations). Repo `devrc`,
   file `scripts/present/measure.py`.
5. **Make the pre-push gate change-scoped, so a typical push costs seconds instead of ~9 min.**
   `innovation-upstream/devrc#952`. 🔴 **This is the lever that makes step 2 cheap, and it is ~90%
   already built** — `should_run_by_files` (`githooks/tests-on-push.sh:~152`) already walks the
   pushed refs and computes `git diff --name-only` per range, then **throws the file list away** to
   answer a boolean against `CODE_RE='^(scripts/|flake\.nix$|flake\.lock$)'`. So touching one file
   under `scripts/` runs all 31 targets: #908's own push was a shell installer and paid full freight
   to learn nothing about `browser-bridge` or `dl-router`. Replace the boolean with a path→target
   map; keep the existing **fail-toward-running** discipline for anything ambiguous; let shared
   infra (`testlib/`, `conftest.py`, `flake.*`) still run everything; leave CI running the full set
   so the pre-push gate is fast feedback rather than the sole authority.
   ⚠ **Ranked 5, not 4, deliberately — ranks are never renumbered here** (a claim slug is
   `<doc>-<rank>`, so inserting at 4 would silently re-point a live claim on the laptop item).
   ⚠ **This does NOT get the FULL suite to 60s and nothing credibly will** — that is 9x on work
   which is mostly waiting and process-spawning. Injectable deadlines plus splitting the two hot
   files might reach 3–4 min; past that you are deleting real integration coverage.
4. **Deploy to the laptop** — `home-manager switch` there picks up the keepalive. Unverified;
   `zach@192.168.50.155`.

## Gotchas / decisions / dead-ends

- 🔴 **RUNNING STEP 2 AS THIS DOC ORIGINALLY WROTE IT WOULD HAVE BRICKED GIT ON THE WHOLE BOX.**
  Found 2026-08-27 by a **blind** pre-merge audit of #908 (the diff and a checklist, deliberately
  *not* the prior round's conclusions — a framed audit verifies the frame). `CDPATH` is **exported**
  on this host (`.:/home/zach/workspace:/home/zach/workspace/civit`), and bash `cd` **echoes** the
  directory whenever it resolves one via CDPATH. So a **relative** invocation — `githooks/install.sh`,
  the shape the installer's own header documented and the shape step 2 invited — made the command
  substitution capture cd's echo *and* `pwd`, and `$DIR` became a **two-line string**. `$DIR` is
  interpolated into the provenance stamp, so line 2 landed in `~/.gitconfig` **as config**:
  every later git command in every repo died `fatal: bad config line 4`, including the `--uninstall`
  that would repair it. Recovery was manual only. At `origin/main` this was inert (the installer died
  at its first `--global` write); **#908 is what made it reachable**, so the ladder caught it in the
  exact window where it existed. Fixed in `648f08c2` on two independent axes — `CDPATH= cd -P --`,
  plus a `_reject_bad_dir` refusal of any `$DIR` that is not a single-line path to a real directory.
  **A relative invocation is safe now; the absolute form is still the documented one.**
  ⚠ **21 other tracked `.sh` files still carry the unhardened idiom** (`scripts/gate.sh:136`,
  `scripts/run-tests.sh:282`, `scripts/initiatives/run-viewer.sh:23`, …). None writes global git
  config, so none carries this blast radius — the fix was scoped to #908's two files on purpose.
- 🔴 **THE SUITE WAS STRUCTURALLY BLIND TO IT, AND THAT IS THE REUSABLE LESSON.** `_install()`
  always invoked the installer by **absolute** path — the one invocation shape that cannot exhibit
  the bug — so `CDPATH` passed through the fixture's env *inert*. Every test passed, the mutation
  battery was "well-validated", and the defect sat in the dimension the harness **pinned**. Ask what
  dimension your fixture holds constant; widening the harness IS the fix, not extra work.
- 🔴 **Three guards written during that fix were wrong in ways that read as correct — each caught
  only by a control, never by re-reading.** (1) The refusal's first spelling used
  `*"$(printf '\n')"*`; command substitution **strips trailing newlines**, so the pattern was the
  empty string, degraded to `**`, and refused every well-formed `$DIR` — 14 failures. (2) Two
  mutants **SURVIVED** and were nearly filed as a coverage gap; they survived *correctly*, because
  three independent defences meant no single-line mutation could defeat them all (the fix: consolidate
  to one refusal path, then a two-substitution mutant). (3) A regression test's skip was retied to the
  installer's `rc`, which **silently disarmed it** at the pre-fix tip — the installer creates the
  corrupt file and *then* exits non-zero downstream, so the test skipped over the very corruption it
  existed to catch. Only re-running the red control showed it. **Re-run the red control after every
  edit to a test, not just after edits to the code it guards.**
- 🔴 **`githooks/install.sh` cannot write global git config on a home-manager host.** `git config
  --global` resolves to `~/.config/git/config`, a **symlink into the read-only nix store**;
  `~/.gitconfig` does not exist. The installer dies `could not lock config file … Read-only file
  system`, rc=255, on its **pre-existing** `core.hooksPath` line. **This is why `core.hooksPath`
  reads empty on this host** — #782 and I both attributed that to another session toggling it. It
  had simply never been installable. (Issue #905; fix in #908.)
- 🔴 **The first version of #887 put the fix in `install.sh` and was therefore INERT**, on the
  reasoning "the installer already writes global git config". That reasoning was false on the
  machines devrc targets. An audit caught it. **`nix/programs/git/default.nix` is the repo's
  declarative home for git config** — it GENERATES the read-only file.
- 🔴 **`ssh -G git@github.com` is the WRONG INSTRUMENT for this fix and answers anyway.** It reports
  `serveraliveinterval 0` even with the fix fully live, because it reads `~/.ssh/config` — which was
  deliberately not modified — while git passes the options on the **command line**, where `ssh -G`
  cannot see them. Reading that zero as a result would report a working fix as broken.
- 🔴 **`~/.ssh/config` is a plain unmanaged file here** (`readlink -f` resolves to itself, not into
  devrc or /nix/store). A fix placed there protects one machine and never ships. That is what ruled
  out the issue's own option 2 as written.
- 🔴 **`GIT_SSH_COMMAND` (env) BEATS `core.sshCommand` (config)**, so any script exporting it
  bypasses the fix. Two do; both now carry the keepalive, with a ledger test that fails when the
  set GROWS. ⚠ `claim-work.sh` uses `${GIT_SSH_COMMAND:-…}` — an **inherited** outer value wins and
  the ledger cannot see it (it reads the literal default). Known blind spot, documented in-file.
- 🔴 **`ServerAliveCountMax` defaults to 3.** Setting only the interval silently creates a
  `30×3 = 90 s` disconnect trigger on **every** git-over-ssh operation, where previously only
  `TCPKeepAlive` applied and a stall of any length survived. Set to **6** (180 s) explicitly. This
  is the fix's own failure mode — a trade, not a free win.
- 🔴 **A wrapper's trailing command swallows the push status.** `git push … ; echo; tail` reported
  **exit 0** twice while the real rc was **141** and no branch existed. **Verify a push with
  `git ls-remote`, never the wrapper's exit code.**
- 🔴 **`printf … | grep -q` returns 141 under `set -o pipefail`** when the match is early and the
  output large — `grep -q` exits first, `printf` takes SIGPIPE. Found in #908's mutation harness:
  `CONTROL-KILL` scored **SURVIVED against a run with 10 genuinely failing tests**, and in the
  negated form (`! … | grep -q failed`) it reads as *"no failures"* — **a false green that would
  certify every mutant against a broken baseline.** Grep a **file**, not a pipe. Same SIGPIPE
  family as #782 itself, in a completely different place.
- 🔴 **Editing a file that a background job is currently reading invalidates that run — and bash
  re-reads scripts by BYTE OFFSET, so editing a running script corrupts it mid-execution.** Hit
  **five times** this session: a probe reported `rc=127` from a corrupted fragment; three full-suite
  runs graded a moving tree; an audit agent found the file changing under it. **Fix:** `sha256sum`
  the touched files before a long run and `sha256sum -c` after — that check is now part of the
  procedure, and it is what makes a green run evidence about the tree you committed.
- 🔴 **Never run `handoff_doc.py`/commit from the devrc base clone.** It is shared and other
  sessions move its branch constantly — observed on `main`, then `docs/handoff-783-decision`, then
  `feat/rig-control-toggle-and-timers` **within one session**. Work from a worktree off
  `origin/main`.
- 🔴 **`home-manager switch` deploys whatever branch the shared clone happens to be on.** Mine
  landed in a **five-minute window**: `merge origin/main` at 20:27, generation built 20:29, another
  session checked out a different branch at 20:34. Three minutes later and it would have deployed
  their branch **with a clean success**. Sync, then switch, then verify by live state — and check
  `branch --show-current` first.
- 🔴 **`clawgatectl.nix` builds from the host's homelab-talos WORKING TREE**, so a stale checkout
  silently rebuilds an OLD version and reports success. Checked before switching: tree carried
  `buildVersion = "0.8.1"`, matching the live server. It stayed 0.8.1 after.
- 🔴 **A FALLBACK THAT DISABLED `rebase.autoStash = false` WOULD BE WORSE THAN THE BUG.** #908 adds
  `~/.gitconfig`; that guard is devrc's fail-closed protection against the repo-global stash that
  has already stolen work between sessions. Measured outside any repo: it survives. Verify this
  again if the fallback logic ever changes.
- ⚠ **`git config --global --list` narrows after #908** — once `~/.gitconfig` exists it is the only
  file `--global` *reads*. Effective config is unchanged. Second-order: global writes now **succeed**
  where they previously failed closed, landing in the file that **outranks** the home-manager one —
  a new drift vector for a repo that deliberately pinned settings in nix.
- ⚠ **"No script in devrc reads `git config --global`" is FALSE** and was asserted in three places.
  `scripts/run-tests.sh:2183` and `:2490` and `scripts/present/measure.py` all do. Corrected in
  #908 and in a public comment on #905.
- 🔴 **THE PATTERN OF THIS SESSION, worth more than any single fix: almost every defect after the
  first was a CLAIM RECORDED AS SETTLED FACT, not a logic error.** Seven audit rounds on #887; the
  code was mostly right and the *sentences about the code* were the unreliable part. Twice the false
  claim was mine — including clearing a surviving mutant as "fails safe" from a fixture that
  structurally could not show the failure, then writing that conclusion into the tree. **An
  assertion of safety is what stops the next reader looking.** Corollary that keeps paying: when a
  guard's docstring names a relationship, check the body is as wide as the sentence.
- **Dead end, do not repeat:** `nix-store --realise` on the generated gitconfig path to inspect it
  pre-switch — it is not built yet. `nix eval` of the option, plus the derivation **hash changing**,
  is the cheap pre-deploy evidence that the artifact will differ.

## How to verify
```bash
# the keepalive is LIVE on this host
git config --global --get core.sshCommand      # -> ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6
readlink -f ~/.config/git/config               # -> /nix/store/1yxm38gq…-hm_gitconfig
# 🔴 NOT `ssh -G git@github.com` — it reads ~/.ssh/config, which is deliberately untouched, and
# will report `serveraliveinterval 0` while the fix works perfectly.

# the fix is on main (CONTENT, never ancestry — #887 was squash-merged)
git -C ~/workspace/devrc show origin/main:nix/programs/git/default.nix | grep -n sshCommand

# 🔴 THE probe that actually closes #782 — reproduce the original symptom.
#   A pre-push hook that sleeps > ~360s, pushing a REAL content change, with GIT_SSH_COMMAND UNSET
#   so the keepalive can only come from config. Expect rc=0 and the branch present.
#   Verify by `git ls-remote`, NEVER by the wrapper's exit code. Delete the scratch branch after.
#   Arm the hook with a REPO-LOCAL core.hooksPath in a throwaway clone — never `githooks/install.sh`,
#   which sets it GLOBALLY for every session on the box.

# #908 landed — by CONTENT, never by ancestry (it was SQUASH-merged, so
# `merge-base --is-ancestor` returns FALSE forever and that is NOT "unmerged").
gh pr view 908 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit
git -C ~/workspace/devrc show origin/main:githooks/install.sh | grep -n 'CDPATH= cd -P\|_reject_bad_dir'

# the gate is NOT armed until someone runs the installer — re-checked 18:50Z, still unset
git config --global --get core.hooksPath       # -> empty, expected, until step 2 above
ls ~/.gitconfig                                # -> absent, expected, until step 2 above
```
