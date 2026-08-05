# RULES — ARCHIVE (worked incidents; NOT auto-loaded)

**This file is not read on every session, and is deliberately NOT concatenated into
opencode's `AGENTS.md`.** `claude/RULES.md` is the core: **every rule that binds you lives
there, at its widest scope.** What lives here is the *evidence* — dates, byte counts, store
paths, PR numbers, the blow-by-blow of how each failure was diagnosed, and the theories that
turned out to be wrong.

🔴 **Never read this file to decide whether a rule applies.** RULES.md is self-sufficient for
that by construction; if you find yourself here trying to work out whether a rule covers your
case, the answer is the preamble's: **read the rule at its widest reading and proceed as if it
does.** Come here for the *why*, to check whether something was already tried, or when you are
about to rewrite a rule and need to know what it was paying for.

**Adding to this file:** each entry is anchored by the `→ archive: <anchor>` tag in RULES.md
and names the rule it supports, so the link works in both directions. New worked incidents go
here, not into the core — the core's ceiling is enforced by `scripts/tests/test_rules_size.py`.

Text below is preserved from `claude/RULES.md` as it stood at commit `26d5268` (2026-08-02),
the last revision before the core/archive split.

---

## Table of contents

**Verification Honesty**
- [honest-phrasings](#honest-phrasings)
- [green-and-audited-but-broken](#green-and-audited-but-broken)
- [deploy-vs-consumer](#deploy-vs-consumer)
- [dirty-tree-probe](#dirty-tree-probe)
- [audit-fix-resets-gate](#audit-fix-resets-gate)
- [unreachable-guards](#unreachable-guards)
- [vacuous-guards](#vacuous-guards)
- [mutation-sweep-blind-spots](#mutation-sweep-blind-spots)
- [nine-broken-harnesses](#nine-broken-harnesses)
- [positive-control](#positive-control)
- [parsing-tool-output](#parsing-tool-output)
- [isolation-seam](#isolation-seam)
- [spelled-guards](#spelled-guards)

**Memory / Failure Investigation**
- [stale-observation](#stale-observation)
- [empty-result](#empty-result)

**Deterministic Over Prose**
- [consolidation-finds-bugs](#consolidation-finds-bugs)

**Green Test Suite**
- [merged-tree](#merged-tree)
- [two-tiers](#two-tiers)
- [declarations-vs-instances](#declarations-vs-instances)
- [count-not-exit-code](#count-not-exit-code)
- [config-blind-suite](#config-blind-suite)
- [flake-vs-assertion](#flake-vs-assertion)

**Git Workflow**
- [wrong-branch-writes](#wrong-branch-writes)
- [stranded-docs](#stranded-docs)
- [stash-incidents](#stash-incidents)
- [sops-retraction](#sops-retraction) 🔴 **RETRACTED THEORY — do not re-derive**
- [worktree-envrc](#worktree-envrc)
- [base-clone-drift](#base-clone-drift)

**Shell & Tooling**
- [readlink-arbiter](#readlink-arbiter)
- [zsh-unbraced-var](#zsh-unbraced-var)

---

## honest-phrasings
*Supports: "Deployed ≠ verified" (Verification Honesty) and "Write tool over heredoc-to-file"
(Token & Tool Hygiene).*

The ✅/❌ pairs that used to sit inline in the core. Both restate rules the surrounding bullets
already state, so they live here rather than being paid for every session.

**Verification Honesty**

- ✅ "Deployed. Reproduced the FAB click via Playwright — modal opens. Verified."
- ❌ "FAB fixed and verified on-cluster." (the rollout succeeded; the click still does nothing)

**Token & Tool Hygiene**

- ✅ `Write` tool to create `/tmp/build.sh`; Read `foo.go` once, then Edit it
- ❌ `cat > /tmp/build.sh << 'EOF' … EOF`; Edit a file never Read this session

## green-and-audited-but-broken
*Supports: "Reproduce the original symptom" (Verification Honesty).*

A full green suite AND a clean adversarial audit are only prerequisites. Four features in one
session passed both while being broken in reality: an inert code path, a feature that stole the
operator's screen on every read, a feature that could not start at all, and world-readable
secrets on disk.

## deploy-vs-consumer
*Supports: 🔴 "A deploy reporting success is a claim about the DEPLOY, not about the CONSUMER."*

Measured 2026-08-02: `ship.sh` reported `✅ VERIFIED — on branch main at origin/main + switched`
on the workbench while the `browser-bridge` `systemd --user` unit was crash-looping on
`OSError: [Errno 98] Address already in use` — an **orphaned process from the previous day
(started Aug 1 16:18, in NO systemd cgroup)** held `127.0.0.1:8788` and was serving the OLD
`server.py`. Every "deployed" claim about that host would have been measured against the
orphan. The converge check verified branch + switch and structurally *could not* see that the
service never started.

## dirty-tree-probe
*Supports: 🔴 "A live probe against a DIRTY tree is evidence about the DEPLOYED ARTIFACT."*

Measured 2026-08-01: `browser context` was shipped into `protocol.js`, the service worker, the
CLI and `manifest.json` but never into `server.py`'s `ALLOWED_OPS`, so it was **dead on `main`**
— it probed green only because one host had an *uncommitted* `server.py` fix that a
`home-manager switch` had baked into the deployed copy. Three agents reported the correct
failing test and were overridden by the probe.

## audit-fix-resets-gate
*Supports: "An audit/review fix RESETS the verification gate."*

Measured twice in one session: an adversarial audit correctly identified a missing own-tab
`tabId` check; applying it silently ate every nested CDP event and shipped a **completely
inert** feature that still passed 428 green tests and a second clean audit. The audit was right
about the gap and the fix was wrong about reality.

## unreachable-guards
*Supports: "A guard must be proven REACHABLE, not just breakable."*

Three ways a passing mutation test still leaves a guard untested:
(a) **an earlier check always wins**, so the guard can never execute at all — a cap shipped this
way and could provably never fire;
(b) **a DIFFERENT guard's error kills your test**, so the test is green for the wrong reason and
passes with your guard deleted — two tests "proving" a cap were being killed by another guard
entirely;
(c) **the happy path resolves anyway**, the state clears itself, and the assertion passes with
the guard defeated.

## vacuous-guards
*Supports: "A regression test must be shown to fail on pre-change code."*

Four vacuous guards on a single PR in one session; two got through review and were caught only
by an adversarial audit. One "invariant guard" passed because its click landed outside the
viewport, so the interaction never happened.

## mutation-sweep-blind-spots
*Supports: "Mutation-test a guard before certifying it."*

An 18/18 and a 20/20 sweep each had blind spots that only a *differently-constructed
independent* sweep found — including a mutant that dropped one component from a signature and
**survived**, silently reintroducing the very bug the PR fixed, because the value-pin test
happened to use a fixture where `pid == pgid == sid` and could not discriminate.

## nine-broken-harnesses
*Supports: 🔴 "Validate the HARNESS against a known-bad state before you read its verdict."*

**Nine** harnesses in one session (2026-08-01) reported success while testing nothing:
1. a runner absent from `PATH` (so *every* mutant exited non-zero);
2. `diff` defaulting to unified output, so a byte-identical control "passed";
3. a seed tree indistinguishable from its target;
4. a bash subshell inheriting `$$`;
5. a crashed sweep that left a mutation applied and poisoned the next baseline;
6. a `Promise.race` against a spinner whose dangling promise hung the whole file instead of
   reporting;
7. the repo's own flake gate skipping a suite for want of `curl`;
8. and 9. two frontmatter extractors that re-matched `---` in the document body and reported
   false parse-failures.

A tenth shape, 2026-08-05: a **negative control built from a textbook fixture**. `gitleaks`
allowlists canonical example credentials, so a control file containing nothing but
`AKIAIOSFODNN7EXAMPLE` + the documented AWS example secret scanned as `no leaks found` — the
harness reported clean while being structurally unable to see the very thing it was pointed at.
A canary built from realistic-looking values reported `leaks found: 2`. Build the bad case from
realistic data, never from the vendor's own example pair.

## positive-control
*Supports: 🔴 "A harness that COUNTS needs a POSITIVE control too."*

Ground case (2026-08-01): four consecutive "0 submits" results were treated as evidence a guard
held; they only became evidence once one clean run submitted **exactly 1** through the same
counter.

## parsing-tool-output
*Supports: 🔴 "When you PARSE a tool's output, its format is a dependency you did not pin."*

The [nine-broken-harnesses](#nine-broken-harnesses) list includes `diff`'s unified default as
one item; it is a **CLASS, not that one manifestation** — that rule was READ in-session and the
trap was hit anyway in a new shape. Three on 2026-08-02:

- `diff` emitted unified output (`---`/`+++`/`@@`), so greps for `^>`/`^<` matched **nothing**
  and reported "0 lines differ" for files that differed by **1,445 bytes** — a false CLEAN,
  where the recorded case was a false PASS, and only `cmp` disagreeing settled it;
- node 24's TAP/spec reporter change made a `^# (tests|pass|fail)` grep return empty, read as
  "no output" rather than "wrong pattern";
- `rc=$?` after a pipeline read `echo`'s status instead of the command's.

## stale-observation
*Supports: 🔴 "'Remembered' includes what YOU observed earlier in this same session."*

Ground case (2026-08-01): a cache row was inspected and correctly found to be a hand-seeded
stub, so deleting it was safe; by the time the delete actually ran the operator had dogfooded
the app and the real **4,661,987-byte** payload had landed in that row, so the delete removed
live data. **The impact was nil** — the cache has a designed empty state and repopulates on the
next run — which is precisely why it is worth writing down: nothing failed, nothing alerted, and
the reasoning was wrong anyway.

## empty-result
*Supports: 🔴 "An EMPTY RESULT cannot distinguish two mechanisms."*

Ground case (2026-08-01): an empty `settings` table is produced *equally* by a click blocked
client-side (the request was never sent) and by a server that received the request and answered
**400**. The table cannot tell them apart — **the network can**:
`performance.getEntriesByType("resource")` showing **zero** entries for the endpoint proves the
request never left the page, which is the former and only the former.

## merged-tree
*Supports: 🔴 "Gate on the MERGED tree, not the PR branch."*

Four PRs in one remix batch were all individually green and individually audit-clean; **two were
red** — one a pure cross-PR interaction (PR A's feature deleted the DOM nodes PR B's tests
queried), one regressing pre-existing tests it never ran. Per-PR review structurally cannot see
this: B's reviewer ran before A existed. One PR was 10 commits behind main and had never been
tested against it.

## two-tiers
*Supports: 🔴 "A suite that runs in TWO TIERS must be green in BOTH."*

Ground case (2026-08-02): **one commit shipped three regressions that masked each other.**

1. A FILE added to the runner's target list was rejected by a `[ ! -d ]` guard → the gate went
   red and **913 tests never ran**.
2. `SECRET_PATTERNS` moved to another module, so a drift test parsed `[]` and **fails on any
   host with the hook deployed — but SKIPS in the sandbox**, where the file is absent.
3. Ten `nix-instantiate` tests `pytest.fail()` when the binary is absent, so they **fail only in
   the sandbox** and pass on every dev host.

(2) and (3) are exact complements, and both hid behind (1)'s red.

## declarations-vs-instances
*Supports: 🔴 "A count of DECLARATIONS is not a count of INSTANCES."*

Measured 2026-08-02: a grep of `skipif` decorators found "2 node-related skips", which was
reported as 2 skipped tests and used to size the work. The two decorators actually gated **123
tests** — an entire suite (`initiatives`: 660 passed / 123 skipped in the sandbox, vs 783 passed
/ 0 skipped with `node` present). That 60× error was the difference between a nit and the
session's most valuable fix.

**Three live instances in ONE session — that is a pattern, not a coincidence:** 2 `skipif`
decorators gating 123 tests; **1 list entry gating 913** (a single line in `run-tests.sh`'s
target list that the runner silently rejected); and **1 `nix_eval()` helper gating 10**
parametrized tests, all of which `pytest.fail()` when `nix-instantiate` is off PATH.

## wrong-branch-writes
*Supports: 🔴 "Re-check WHICH branch you are on before ANY write in a shared checkout."*

Observed:
- a `git pull --rebase origin main` issued without re-checking rebased *another session's*
  feature branch onto main (content survived, base moved);
- another session's `git checkout` silently reverted a staged build mid-verification and deleted
  a test-fixture directory;
- a session-handoff doc committed "to `main`" landed on a dispatched subagent's branch — `main`'s
  reflog showed it had **never moved**, and a `git push origin main` at that moment would have
  reported success while silently leaving the handoff behind.

`git reflog` is the one-command diagnosis: `checkout: moving from main to <branch>`, then your
commits.

## stranded-docs
*Supports: 🟡 "Docs/notes written into a working tree are UNSAVED WORK."*

Three such pieces were found stranded in one session: a production false-outage post-mortem,
three measured browser gotchas, and a 288-line `standup.sh` (vs 272 in `main`).

## stash-incidents
*Supports: 🔴 "`git stash` is repo-GLOBAL — never `git stash` in a shared repo, for ANY reason."*

Two parallel remix subagents stole each other's work this way (2026-07-25) — that is the
evidence the rule rests on, and it is unaffected by the [retraction](#sops-retraction) below.

**Broadened 2026-08-01.** The heading used to read "never use it to clear a tree for a rebase",
and that scoping is exactly how it failed. A subagent in `civitai` stashed for a completely
different reason — clearing what it believed was a dirty tree to measure a test baseline — read
the rebase-shaped rule as not applying, and proceeded. The `stash push` silently no-op'd on an
already-clean file, so the following `stash pop` reached for **a teammate's entry** off the
shared stack. The pop conflicted, which is the only reason the entry was kept rather than
dropped; 58 stash entries and `stash@{0}` were verified intact afterwards. It knew the rule and
was bitten anyway, because "hazardous for rebases" is not "don't". This incident is also the
worked example in RULES.md's opening widest-reading rule.

## sops-retraction
🔴 **RETRACTED THEORY — do not re-derive it.**
*Supports: 🔴 "When a file keeps reverting, find the WRITER before blaming the VCS operation."*

The stash rule used to also cite the `stash → pull --rebase → stash pop` autostash as having
corrupted `.sops.yaml` on a dirty tree (2026-06-24). **That attribution was wrong** (retracted
2026-07-31).

`.envrc` was regenerating `.sops.yaml` from a `.sops.template.yaml` frozen at 9 rules on *every
direnv load*, silently reverting the tracked 31-rule file — a pure 113-line deletion that
dropped 22 app rules and the fail-closed catch-all, after which a new `*.enc.yaml` in an
unlisted path would commit in **plaintext**. Proven by rendering the frozen template to a
byte-identical sha256 of the corrupt file, and by its mtime matching a `.direnv` rebuild to the
second.

The wrong theory is *why the bug survived four recurrences from 2026-06-06*: every fix targeted
stash behaviour, so nobody looked for the actual writer. Fixed in `homelab-infra` (generator
removed, stale template deleted, `scripts/check-sops-rules.sh` gates it). A checksum guard
wrapped around the suspected operation proves nothing if the real write happens elsewhere — that
one compared hashes around the stash while the overwrite landed on the next `cd` into the repo.

## worktree-envrc
*Supports: 🔴 "A fresh worktree does NOT inherit the repo's dev environment."*

On civitai (2026-07-31) three subagents each hard-coded a `/nix/store/…` Prisma engine path that
`devShells.default` already exports, every gate ran on system Node 26 instead of the flake's
pinned 22, and the workaround got propagated into later dispatch prompts as if it were an
inherent NixOS trap; **126** civitai worktrees had no `.envrc`.

## base-clone-drift
*Supports: "Re-sync the base clone after worktree work merges."*

homelab-talos was 262 commits behind on 2026-07-30. Its dirty files were *stale orphans* of
already-merged work, not WIP.

The deterministic test for *dirty is not WIP* (2026-08-05): hash the working copy and compare it
against that path's recent committed revisions —

```bash
sha256sum <path>
for c in $(git log -n 20 --format=%H -- <path>); do
  printf '%s %s\n' "$c" "$(git show "$c:<path>" | sha256sum | cut -d' ' -f1)"
done
```

A byte-for-byte match with an **older** revision proves the file is a stale orphan of
already-merged work, not work in progress. Measured on two dirty files that matched a revision
**four months** old; treating them as WIP and "restoring" them would have reverted four months of
changes and reinstated a line that had been deliberately removed.

**Two drift tells** while sorting it out:

- `warning: skipped previously applied commit` during a rebase — that work already landed from a
  worktree; `git rebase --skip`;
- untracked docs blocking a checkout — a worktree committed them already. Diff against upstream,
  then delete the local copy.

## readlink-arbiter
*Supports: "Is an edit to a home-manager-managed dotfile LIVE? `readlink -f` is the arbiter."*

Two files that look identical resolve differently:
`~/.claude/skills/browser/SKILL.md` resolves through the store to
**`~/workspace/devrc/scripts/browser-bridge/SKILL.md`** (`mkOutOfStoreSymlink` — the working
copy **IS** the live file, an edit takes effect immediately), while `~/.claude/RULES.md`
terminates at `/nix/store/…-hm_RULES.md`, a read-only **regular file** — a copy, so editing
`~/workspace/devrc/claude/RULES.md` does nothing until a home-manager switch.

An agent called the browser skill an in-store copy because the two files were byte-identical —
but identity was simply the consequence of their being **one file**, and acting on it meant
either an unnecessary rebuild or, worse, treating a live edit as inert.

## zsh-unbraced-var
*Supports: 🔴 "zsh's unbraced `$var` is NOT bash's — two traps." (Shell & Tooling).*

**(a) No word-splitting.** `for x in $SPACE_SEPARATED` loops **once** with the whole string as
`$x`; a following `${x%%:*}`/`${x##*:}` then silently grabs the wrong field. Bit prod: a
`node:ip` loop ran one iteration and applied one node's Talos machineconfig to the **last**
node's IP.

**(b) History modifiers apply inside parameter expansion.** Measured 2026-08-03 under `zsh -f`:

```
B=feature/foo
"origin/$B:e2e/uxaudit/fakes.go"    →  origin/2e/uxaudit/fakes.go     # WRONG, silent
"origin/${B}:e2e/uxaudit/fakes.go"  →  origin/feature/foo:e2e/uxaudit/fakes.go
```

zsh parses `$B:e` as `$B` with the **`:e` (extension) history modifier**; `feature/foo` has no
extension, so it expands to the empty string and the literal `2e/uxaudit/fakes.go` survives.
The result is a well-formed path that is simply *wrong* — `git show` then reports a plausible
"does not exist in" error about a ref nobody typed, so the failure reads as a missing file
rather than a quoting bug. bash does not do this.

The modifier set is `e` (extension), `h` (head), `t` (tail), `r` (root), `s` (substitute) and
`gs`, so the collision surface is any `$var` immediately followed by `:` plus one of those
letters — git refs (`$B:path`), `rsync`/`scp` targets (`$HOST:/path`), and timestamps.

**(c) The mirror image, in a test wrapper (2026-08-05).** Where (a) is zsh *not* splitting when
you expected it to, this is a wrapper splitting when it must not. A runner interpolated bare
unquoted `$*` into an inner shell:

```
npx playwright test $*          # WRONG
npx playwright test "$@"        # or, when re-entering a shell:  printf ' %q' "$@"
```

Invoked as `run.sh -g "some phrase"`, the quotes are gone by the time the inner shell parses the
line, so `some` and `phrase` become **filename filters**. Playwright matched no files, printed
`No tests found` and **exited 0** — zero tests run, reported as success. The generalisable half:
a wrapper that answers "nothing to do" instead of erroring is how a green gets believed; make a
zero-selection case a non-zero exit.

## config-blind-suite
*Supports: 🔴 "A test that skips itself, or passes by accident of the environment, is worse than
no test — and a suite whose CONFIG pins a dimension is STRUCTURALLY BLIND to that dimension's
bugs." (Green Test Suite).*

Two ground cases, both where the *harness config*, not any individual test, decided what could be
observed:

- **Headless default window.** Two tests passed only because the headless browser's default
  window is 437 px tall; setting a realistic viewport failed them on clean `main`.
- **Desktop-only Playwright project (2026-08-05).** The suite ran a single project pinned to
  `devices['Desktop Chrome']` with `hasTouch: false`, so a responsive breakpoint was permanently
  on the desktop side and **not one test in the suite had ever rendered the component at phone
  width**. Every mobile defect passed it vacuously. The consequence that matters: writing new
  specs into that config to "cover" a mobile fix would have produced tests that pass whether or
  not the fix works — so widening the config (adding a mobile project) was part of the fix, not
  extra work.

## flake-vs-assertion
*Supports: 🔴 "Distinguish a real failure from a load flake by WALL TIME — and by WHOSE time
moved." (Green Test Suite).*

Worked example, 2026-08-05: a spec failed at **16.5 s** against a **2.4 s** norm — a ~7x
inflation that reads as load at a glance. Decomposed, 16.5 s is the 2.4 s baseline + one 10 s
Playwright `expect` timeout + teardown: the arithmetic of a **single failed assertion**, not of a
loaded box. The discriminator is distributional, not a ratio: **load inflates every test in the
run, a failed assertion inflates exactly one.** Read the sibling tests' durations in the same run
before calling it load.

## count-not-exit-code
*Supports: 🔴 "COUNT the tests; never read an exit code." (Green Test Suite).*

Four separate false greens in one session:

1. a wrapper's trailing `echo`, whose exit status replaced the suite's;
2. a trailing `grep` with no match (exit 1) at the end of a pipeline;
3. a suite truncated by `panic: test timed out`, which **still reported `FAIL=0`**;
4. a piped `grep` inside `nix-shell --run`, where the runner's status was lost.

A known-red slow test must be `-skip`ped BY NAME, or it eats the suite budget and silently
truncates everything after it — that truncation hid two regressions through two "green" full
runs.

## consolidation-finds-bugs
*Supports: 🔴 "Consolidation is also a BUG-FINDING instrument, not just hygiene."*

Consolidating a duplicated predicate is normally filed as cleanup. On civitai-manager PR #59
(`fe346b5`) **one** gate consolidation produced **two** findings — and it is the second that
makes the point, because nobody was hunting it.

**1. The AUDIT finding — a false "Ready" on a graph the run path would refuse.** Two surfaces
answered the same question about the same graph: `realRun` ("may I submit this to ComfyUI?")
and `workflowReadiness` ("may I tell the user it is ready?"). The readiness copy was strictly
WEAKER — it keyed on the three preflight COUNTS plus conversion warnings and never read
`report.OK`. An adversarial audit measured **6 of 10** probed unusable inputs rendering
`ready`, all reachable end to end through `POST /workflows/import-png`.

**2. The CONSOLIDATION finding — `realRun` itself SUBMITTED `{}` to ComfyUI.** Nobody was
looking for this; it fell out of putting the copies side by side. Verified against the
**pre-fix** tree at `fe346b5^`, not inferred from the fixed code:

- `Preflight` returned `PreflightReport{OK: false}` **only** on a `json.Unmarshal` error. `{}`
  unmarshals successfully into an *empty map*, so the node loop runs zero times and
  `report.OK = len(MissingNodes)==0 && len(MissingModels)==0 && len(BadOptions)==0` → **true**.
- `realRun`'s never-submit gate was `if convWarned || graphIncomplete || !report.OK`. For `{}`:
  `convWarned` false, `graphIncomplete` false, `report.OK` true → the disjunction is **false**
  → not blocked → **submitted**.
- Corroborated independently by PR #59's delta auditor, which probed **14 payloads** comparing
  old vs new verdict: **3 changed, all stricter, none looser** — `{}`, `null`, and whitespace
  variants. An empirical before/after, not a reading.

Both were consolidated into `internal/web/run_gate.go` — "the never-submit gate, in one place".

**Other instances, same session:**

- **The node-pack `needed()` predicate** — open-coded as `Contested && !Best` at **three**
  sites (the collapse, the Install button's prominence, the contest badge), and all three
  wrong *in the same direction* for the same reason: a pack can lose a contest on one class
  while being the sole provider of another. Fixing only the reported site would have left a
  required pack demoted and mislabelled. Now one method, `rankedPack.needed()`.
- **`canQueue`** — duplicated between `runZone` and the count segment that must agree with
  it; both now derive from `canQueueWorkflow`.

Mechanism: a predicate open-coded at N sites is typically wrong at N−1 of them **in the same
direction**, because each copy was written from the same incomplete mental model. That is why
reading the copies individually finds nothing — they agree with each other. Unifying them is
what makes the disagreement with the *correct* rule audible.

### 🔴 The correction to this entry was itself wrong — and its error class is the better lesson

Finding 2 was briefly **deleted** from this file and replaced with "nothing was wrongly
submitted; only the readiness line said yes", on the strength of reading
`internal/web/run_gate.go`.

**`run_gate.go` did not exist before the fix.** `git log --diff-filter=A -- <path>` shows it
was ADDED by `fe346b5` — the very commit that fixed this. The file being read *was the
remedy*, and the bug was inferred backwards out of it. The inference was coherent and
specific and wrong, which is what makes it dangerous: it produced a plausible history that
deleted a real finding.

**Inferring PAST behaviour from CURRENT source is its own error class.** It is
[dirty-tree-probe](#dirty-tree-probe) rotated into the time axis: there, the artifact you
measured was not the artifact you were claiming about; here, the *revision* you read was not
the revision you were claiming about. Going to read the code is the right instinct — it is
why the session's other corrections were correct — so the failure is invisible from inside
the method. Only the input was wrong.

**The pre-change state is cheap and available. Read it there:**

```sh
git log --diff-filter=A -- <path>   # was this file ADDED by the fix?
git show <fix-sha>^:<path>          # what the code ACTUALLY was, before
```

Corollary: **when the thing you are describing is a fix, the file that fix created is the
worst available witness to the bug.** Prefer an empirical before/after (an old-vs-new probe
across payloads) over any reading of either revision.

## isolation-seam
*Supports: 🔴 ""Verified in isolation" is the new vacuous green."*

Two civitai-manager features shipped in consecutive releases: a pre-click **readiness line** and
a post-failure **panel**. Each had hermetic tests, mutation matrices, live-browser checks and
multiple audit rounds. Clicking Generate on a workflow that could not run rendered **both, 0px
apart**, with the same counts and the same caveat. No test or audit ever constructed that state,
because each was scoped to one surface — B's reviewer ran before A existed, and neither
fixture loaded the other's component.

**Four more of the same shape, all measured in one session:**

- the axe fixture that **never rendered the panel's custom-node half** — so "0 violations" was a
  fact about a surface never loaded. Closing it took captures 24 → 26 and immediately surfaced a
  *serious* keyboard-accessibility bug;
- a lab fixture with `comfy_model_path` already set, so the setup disclosure never rendered;
- a CI job that never compiled the nested `e2e/uxaudit` module;
- `cardInstallBlockedText`, which has **never been axe-scanned in any wording** because it
  renders inside a `<dialog>` the walk never opens.

**The guard that closes a seam pins a RELATIONSHIP, not a component.** Worked example:
`TestEveryRunStatusWriterGoesThroughRunStatusBody` (`internal/web/run_status_writers_web_test.go`)
AST-parses the package's non-test source and asserts a **two-entry ledger** of everything allowed
to call `runStatusFragment` — failing when the set GROWS (a new writer bypasses the owner) *and*
when it SHRINKS (a listed caller no longer exists). It carries a scanner precondition
(`if files < 20 { t.Fatalf }`) so a broken parse cannot pass as "no offenders".

It is paired with a behavioural table (`TestEveryRunStartingEndpointClearsTheReadinessLine`)
because the structural check is blind to a **wrong argument**: passing the wrong id into
`runStatusBody` type-checks perfectly and still writes the line to the wrong workflow. Structure
and behaviour catch different halves; neither alone closes the seam.

## spelled-guards
*Supports: 🔴 "A guard can be SPELLED rather than STRUCTURAL."*

The test is one question: **can this guard pass while the hazard exists in a different shape?**
Four measured instances:

1. **`Contains(body, "disabled")`** — satisfied by htmx's `hx-disabled-elt` attribute on a
   perfectly **live** button. Green for an accidental reason; worse, its failure message
   *instructed* a maintainer to reintroduce the dead button it was meant to forbid.
2. **A "no dead control" guard that knew only the old button's exact label**, so a
   differently-labelled disabled button passed the whole suite.
3. **A newly-added branch with no no-POST guard at all** — a planted POSTing button caused
   **zero** failures.
4. **An invariant asserted at 1 of 14 writers.** Reverting the other 13 — including the one
   response that must clear the line the user is looking at — left the suite green.

The remedy is to assert a **state**, not a spelling: an `id` plus the attribute on the same
element, an ARIA role, or an enumerated set of allowed callers. Any assertion whose subject is a
word that another feature is free to use is a coincidence waiting to be relied on.
