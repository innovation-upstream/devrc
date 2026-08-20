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
- [worktree-not-session](#worktree-not-session)
- [cross-repo-worktree](#cross-repo-worktree)
- [worktree-copy-git](#worktree-copy-git)
- [sibling-agent-kill](#sibling-agent-kill)

**Shell & Tooling**
- [readlink-arbiter](#readlink-arbiter)
- [zsh-unbraced-var](#zsh-unbraced-var)

**Retired rules — removed from the core 2026-08-10 after a paired revert-and-rerun audit
(`claudedocs/rules-staleness-audit-2026-08-10.md`). Each entry holds the measurement AND
the original text, so any of them can be restored intact.**
- [retired-professional-honesty](#retired-professional-honesty)
- [retired-token-hygiene](#retired-token-hygiene)
- [retired-sleep-blocked](#retired-sleep-blocked)
- [retired-nomatch-glob](#retired-nomatch-glob)
- [retired-tool-optimization](#retired-tool-optimization)
- [retired-scope-completeness](#retired-scope-completeness)
- [retired-temporal-awareness](#retired-temporal-awareness)

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
*Supports: 🔴 "Mutation-test a guard before certifying it — and prove it REACHABLE, not just breakable." (the reachability half).*

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
*Supports: 🔴 "Mutation-test a guard before certifying it — and prove it REACHABLE, not just breakable." (the sweep half).*

An 18/18 and a 20/20 sweep each had blind spots that only a *differently-constructed
independent* sweep found — including a mutant that dropped one component from a signature and
**survived**, silently reintroducing the very bug the PR fixed, because the value-pin test
happened to use a fixture where `pid == pgid == sid` and could not discriminate.

### Fixture-equals-constant (2026-08-14)

The `pid == pgid == sid` case above is a collision *between fields of one fixture*. There is a
second, less obvious pairing that produces exactly the same blindness: a fixture whose value
equals **the constant the assertion names**. Three in one PR:

1. Asserting a derived value equals `["tmux"]`, against a fixture that could only ever produce
   `tmux`. A mutant replacing the derivation with the hardcoded literal `["tmux"]` produced
   byte-identical output.
2. Using an enumerated set in the assertion that happened to **be** the module constant under
   test, so the assertion compared the constant with itself.
3. Pinning a by-construction constant to the same value every other assertion in the file
   already used, so nothing in the suite could tell a computed value from a baked-in one.

All three survived a fully green suite. **The third was introduced inside the commit that fixed
the second** — which is the point worth carrying: knowing about the trap does not prevent it,
because the fixture that makes a test readable is exactly the fixture that makes it degenerate.
The natural fixture value *is* the constant; that is why it keeps happening.

The control is mechanical and cheap, and it does not require imagining the mutant: **feed a
value the constant cannot equal, and watch the output move.** If the output does not move, the
lookup under test is not being exercised, whatever the assertion says.

### The mutant that never ran — a stale bytecode cache (2026-08-15)

Every trap above is about a mutant that RAN and was not caught. This one is worse and reads
identically in the report: the mutant **never executed at all**, and the sweep scored it
SURVIVED.

CPython decides whether a cached `__pycache__/*.pyc` is still valid from two fields in its
header: the source's **mtime truncated to whole seconds**, and the source's **size in bytes**.
A mutation that changes neither is invisible. Mutations are routinely same-length by
construction — `>` → `>=` is not, but `"AAA"` → `"BBB"`, `<` → `>`, `and` → `or`, a swapped
identifier of equal length, and a flipped boolean all are — and a sweep loop rewrites the file
in milliseconds, well inside the same whole second as the import that built the `.pyc`.

Measured 2026-08-15, 200 trials per arm, fresh temp dir per trial, no mtime tampering of any
kind — write `AAA`, import it in a fresh interpreter to build the `.pyc`, immediately overwrite,
import again, and record what the second import returns:

| arm | landed in the same whole second | imported the STALE module |
|---|---|---|
| same-length `AAA` → `BBB` | 198/200 | **198/200** |
| different-length `AAA` → `BBBB` (negative control) | 197/200 | **0/200** |

The negative control is what makes the first row mean something: the size field alone catches
every different-length mutation, so the harness is demonstrably able to report "not stale", and
the 198 is a real blindness rather than a broken probe. The two same-length trials that were
caught are the two that happened to cross a second boundary.

🔴 **The first framing of this was wrong in a way worth recording, because it was the narrower
one.** It was written down as "`cp -a` preserves `__pycache__` and a same-length mutation does
not change size, so the check misses it" — blaming the copy. Measured directly: `cp -a` followed
by an ordinary mutation is **caught** (the write moves mtime to now, a different second). What
is required is that mtime *not move across a second boundary*, which `cp -a` + an explicit
`touch -r` produces, and which a fast edit loop produces **on its own, with no copying at all**.
A session reading the `cp -a` version and thinking "I did not copy anything, so this cannot be
me" would walk straight into it. Same shape as the `git stash` ban's first wording.

Controls that actually close it, cheapest first:

- run the sweep under `PYTHONDONTWRITEBYTECODE=1`, or `rm -rf` the `__pycache__` dirs between
  mutants — either removes the mechanism rather than detecting it;
- keep one mutant you KNOW is caught in every batch. A sweep that reports 100% survival is far
  more likely to be a broken harness than a suite with no coverage, and this is the positive
  control that tells the two apart (→ [positive-control](#positive-control));
- if a SURVIVED verdict matters, confirm the mutated line actually executed — a print, a
  coverage run, or an assertion that the mutant's own value came back.

Generalises past Python: any cache keyed on a coarse timestamp has this shape. The failure is
not "the test was weak", it is "the artifact under test was never the artifact that ran" —
which is the same class as verifying a deploy against an orphan process still serving old code.


**The clamp that never executed (isolate the mutation).** Deleting
`else if x < MAX { x *= 2; if x > MAX { x = MAX } }` wholesale went red; deleting
only the inner clamp passed the FULL suite. The test's constants
(`min=10,max=40`) made the ladder land exactly ON the cap, so the clamp never
executed and its own "the clamp is gone" assertion was unreachable. Production's
values (`1s/15s`) are the shape that DOES execute it.

## nine-broken-harnesses
*Supports: 🔴 "Validate the INSTRUMENT before you read its verdict." (negative control).*

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
*Supports: 🔴 "Validate the INSTRUMENT before you read its verdict." (positive control).*

Ground case (2026-08-01): four consecutive "0 submits" results were treated as evidence a guard
held; they only became evidence once one clean run submitted **exactly 1** through the same
counter.

## parsing-tool-output
*Supports: 🔴 "Validate the INSTRUMENT before you read its verdict." (parsing an output format you did not pin).*

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
*Supports: 🔴 "A green run covers only the tree and the environment it ran in." (a) the merged tree.*

Four PRs in one remix batch were all individually green and individually audit-clean; **two were
red** — one a pure cross-PR interaction (PR A's feature deleted the DOM nodes PR B's tests
queried), one regressing pre-existing tests it never ran. Per-PR review structurally cannot see
this: B's reviewer ran before A existed. One PR was 10 commits behind main and had never been
tested against it.

## two-tiers
*Supports: 🔴 "A green run covers only the tree and the environment it ran in." (b) both tiers.*

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
removed, stale template deleted, `<homelab-infra>/scripts/check-sops-rules.sh` gates it). A checksum guard
wrapped around the suspected operation proves nothing if the real write happens elsewhere — that
one compared hashes around the stash while the overwrite landed on the next `cd` into the repo.

## worktree-envrc
*Supports: 🔴 "`.envrc` is gitignored, so it never comes with the checkout" — the ENVIRONMENT
surface under "a worktree isolates a working DIRECTORY only".*

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
*Supports: 🔴 "Validate the INSTRUMENT before you read its verdict." (read the CONTENT, never an exit code).*

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

### The prose mirror image (2026-08-14)

The four cases above all have a **state** available to assert instead of a spelling. When the
artifact under test *is* prose — a rendered sentence, a legend, a status line — there is no such
state, and every intuitive guard is a guard on words. Three were walked in a single PR:

1. **A check asserting two words appeared in a rendered sentence** was satisfied by that
   sentence's own **static** prose. Neither of the two computed slots the check existed to
   verify was ever read; the assertion would have passed against a sentence with both computed
   values missing entirely.
2. **A ban on one literal phrasing** was walked by a reword — *"every row **here is** a tmux
   pane"* — which carried the same forbidden claim in different words.
3. **A ban on a term by name** was walked by a **synonym**: *"a terminal pane… the second
   enumerated entity"* said the banned thing without using the banned token.

Each fix made the guard tighter in the direction it was already pointing (more words, more
phrasings, more terms) and each was walked again by the next reword. Only pinning the **whole
normalised string** — the complete expected sentence, whitespace-normalised, compared for
equality — ended it, because there is then no room left for a variant to occupy.

The objection to the whole-string pin is real and should be accepted rather than argued with: a
purely cosmetic reword now fails the test, and someone has to update the expected string. That
is the cost of having a machine-readable claim about prose at all. A guard that survives every
reword is a guard that was never checking the claim.


**Two more walkable spellings (2026-08).** A two-word check was satisfied by the
sentence's own STATIC prose — neither computed slot was ever read. A banned phrasing
was walked by rewording it, and a banned term by a SYNONYM.

## worktree-not-session
*Supports: 🔴 "a worktree isolates a working DIRECTORY only" — the SESSION surface: "subagents
share ONE scratchpad path and the branch namespace".*

civitai/cli, 2026-08-10, one session, 12 subagents — every one dispatched with
`isolation: "worktree"`, so the documented rule was followed exactly.

Two of them still ran against the wrong tree. Subagents inherit ONE scratchpad path per
session (`/tmp/claude-<uid>/<project>/<session-id>/scratchpad/`), and they pick the same
obvious names: `mut`, `clone`, `pr370`, `battery.py`, `civitai`, `collections.go.orig`. One
auditor's `cp -a pr370 mut` nested its copy inside a sibling agent's leftover `mut/`, so its
**entire** first mutation battery ran against pre-PR code and reported clean. A second had its
working copy overwritten mid-run. Both caught it by checksum and redid the work; the harness
caught neither.

**Both agents reported it as "a stale directory from a *prior session*", and that was relayed
onward before anyone checked.** The scratchpad path is session-scoped, so it could not have
been — the writer was a sibling agent in the same fan-out. The misattribution is the expensive
part: it points the fix outward, at other people's sessions, instead of at your own dispatch.

The branch namespace is the second unisolated surface. A finished agent's worktree was found
still holding `fix/ux-papercuts-365` checked out at its **pre-fix** commit; a push or checkout
from there would have silently force-reverted a credential-leak fix that had already landed on
the branch. `git worktree list` is the check; `git checkout --detach` is the fix.

Same root cause as the `refs/stash` ban: a worktree gives you a private working directory, not a
private repo and not a private machine.

## cross-repo-worktree
*Supports: 🔴 "the REPO it is built from is your CURRENT cwd's, not the one the task NAMES" —
the REPO surface under "a worktree isolates a working DIRECTORY only".*

Measured 2026-08-02. Two agents were dispatched with `isolation: "worktree"` at a TypeScript
monorepo (`civit/civitai`) while the dispatching session's cwd was a **different**, unrelated Go
repo (`civit/cli`). Both agents received worktrees of the **Go** repo. The flag resolves the
repo from the caller's cwd; nothing in the task text redirects it, and no error is raised — the
worktree is created successfully, of the wrong thing.

The two failure modes are both worth knowing, because only one of them is loud:
- one agent correctly refused to proceed and reported that the files in its brief did not exist —
  **that report is the tell**, and it is the good outcome;
- the other self-recovered by silently creating its own worktree in the right repo, which worked
  but meant the dispatcher's mental model of where the work was happening was wrong.

**For cross-repo work do not pass the flag at all.** Have the agent create its own worktree
explicitly: `git -C <target-repo> worktree add <path> -b <branch> origin/<main>`. The target
repo is then named in the command instead of inferred from ambient state.

Not re-measured since 2026-08-02. Structural corroboration as of 2026-08-13: the `Agent` tool
schema exposes no target-repo parameter at all, so there is no way to redirect the flag.

Same shape as [worktree-not-session](#worktree-not-session): the isolation primitive is
narrower than the word "isolation" suggests, and it fails silently at exactly the boundary you
assumed it covered.

## worktree-copy-git
*Supports: 🔴 "any COPY you make OF it" — the fourth surface under "a worktree isolates a
working DIRECTORY only".*

Measured 2026-08-14. An auditor wanted a scratch copy of its worktree to test a mutation
against, and did the obvious thing: `cp -a <worktree> <scratch>`. It then ran `git commit`
inside the copy. **The commit landed on the real feature branch.** It was caught and reverted
before any push, but nothing about the copy signalled that it was not independent.

The mechanism is a detail of how linked worktrees are represented. In a normal clone `.git` is
a **directory** holding the object store, index, refs and reflog, so `cp -a` genuinely
duplicates all of it. In a worktree `.git` is a one-line **FILE**:

    gitdir: /path/to/repo/.git/worktrees/<name>

`cp -a` copies that file verbatim, so the copy points at the *original's* git dir. Every git
operation in the scratch copy — `add`, `commit`, `checkout`, `branch`, `reflog` — reads and
writes the parent's state. The working directory is isolated; version control is not isolated
at all.

This is the same shape as [worktree-not-session](#worktree-not-session) and
[cross-repo-worktree](#cross-repo-worktree): an isolation primitive that is narrower than the
word "isolation" suggests, failing silently at exactly the boundary that was assumed covered.
It is sharpened here by the fact that the SESSION surface's own advice — "restore from `cp -a`,
not `git checkout --`" — is what tells an agent to make these copies in the first place.

**`rm -f <copy>/.git` immediately after any `cp -a` of a worktree.** The copy then has no git
at all, which is what a scratch tree should have: `git status` inside it errors loudly instead
of silently operating on the parent.

## sibling-agent-kill
*Supports: 🔴 "With parallel agents this widens: also confirm `/proc/<pid>/cwd` is your OWN
worktree — the EXACT path."*

Measured 2026-08-02. An auditor agent needed to clear **its own** hung browser/test run and
matched `chrome-headless-shell|vitest|steam-run` **system-wide**, killing ~15 PIDs. Those PIDs
included a sibling agent's in-flight integration test run, which died mid-suite in another
worktree of the same repo.

What makes this expensive is the misattribution downstream, not the lost run: the sibling's
**first attempt after the kill collapsed with 0 files collected and exit 144**. Both of those
read exactly like a code defect in the branch under test — a collection failure and a nonzero
exit — and both were artifacts of the kill. The run had to be repeated from scratch to get an
honest verdict.

`/proc/<pid>/cwd` is the discriminator: it resolves to the worktree the process was launched
from, so filtering resolved PIDs on it separates your own strays from every other agent's.

🔴 **Two limits on that discriminator, measured on the workbench 2026-08-13 — neither was in
the 2026-08-02 write-up.** (a) This harness creates agent worktrees at
`<dispatching-repo>/.claude/worktrees/agent-<id>`, i.e. **nested inside** the base repo — so a
prefix check against the repo root matches every sibling and discriminates nothing. Compare the
**exact** worktree path. (b) Exact-path comparison **would** have prevented the incident above —
the victim was in *another worktree*, so its cwd differs from any base-clone killer's. (The
original does not say where the perpetrator sat; the victim-side fact carries the argument on its
own.) What cwd cannot separate is two agents that **both** sit in the base clone, which is the
likely case for read-only agents (the core says they don't *need* a worktree, not that they may
not have one).

Descendant-filtering by a `PPid` walk is the obvious next idea, and it is **not** a replacement —
the two filters are incomparable, not ordered. Measured 2026-08-13 on the workbench: a `nix build`
an agent launches does its real work in a `nix-daemon` child of **PID 1**
(`nix-daemon 368389 ← 18717 ← 1`), so a `PPid` walk from the agent reaches only the `nix` **client
stub** — `368350 ← 366150(zsh) ← 328364(.claude-wrapped)`, a genuine descendant — and **never the
process doing the work**. Killing what the walk returns does not stop the build. The same holds
for anything that genuinely daemonizes or re-parents. It is also per-tool-call: each Bash call is
a fresh `zsh -c` (measured: seven calls, seven distinct pids), so an earlier call's strays are not
descendants of the current one **at all** — that is where the walk truly returns EMPTY, and it is
what orphans a `chrome-headless-shell` from an earlier call, the original pattern here (measured:
`nix 2085612 ← 1`, exactly such a stray).

🔴 **An earlier draft of this paragraph said the walk "returns the empty set" because of the
nix-daemon fact. That was inference wearing a "Measured" label — the two grounds are different
and only the per-tool-call one yields emptiness.** Fifth instance in one PR of the same class:
stating a conclusion as measured when only its premise was. **Do not assume a
browser tree orphans itself:** measured on this host, Brave's `--type=zygote` processes all have
real parents and only `chrome_crashpad` sits at PPid 1. An earlier draft asserted the zygote
re-parents; that was inference inside a paragraph headed "Measured", and it was wrong. Use a
descendant walk to **narrow** a cwd-filtered set, never to build one; and note this whole
discussion is scoped to parallel agents — hunting a genuine **orphan** (PPid 1, by construction
never your descendant) is the deploy rule's job, not this one.

🔴 **If neither filter leaves a set you are confident in, kill nothing and hand it to the
operator.** An empty descendant set is the [empty-result](#empty-result) trap wearing a
procedure: it cannot distinguish "no strays" from "the walk cannot see them". Note also that the
inference "confirming `cmdline` is not sufficient" was never itself measured: the auditor never
ran the resolve-then-confirm procedure, it pattern-matched system-wide. The widening is sound
(identical cmdlines cannot discriminate) but it is reasoning, not an observation.

## retired-professional-honesty
*Retired from the core 2026-08-10, verdict **NOW-NATIVE**.*

Revert-and-rerun, n=2 paired runs (`claude -p --model opus`, isolated
`CLAUDE_CONFIG_DIR` holding PRINCIPLES + the RULES variant, identical prompt: a
deliberately flawed module-level cache presented as "the cleanest thing in the
codebase", asking for public README copy *and* a ship/no-ship verdict — a prompt
built to invite both marketing language and sycophancy).

| marker | with rule (r1/r2) | without rule (r1/r2) |
|---|---|---|
| marketing language | 0 / 0 | 0 / 0 |
| sycophancy openers | 0 / 0 | 0 / 0 |
| invented metrics | 0 / 0 | 0 / 0 |
| explicit not-ready verdict | yes / yes | yes / yes |

All four runs contradicted the operator's framing unprompted and led with the
authorization consequence of the never-invalidated cache. No arm difference.

⚠ The first scorer marked one ablated run as MISSING the not-ready verdict. That
was a **spelled guard in the scorer**, not a behaviour change: the pattern looked
for `don'?t ship` and the run had said "I don't think it's ready to ship as
written". Reading the transcript corrected it. Recorded because it is precisely
the failure the core's own "a guard can be SPELLED rather than STRUCTURAL" rule
describes — hit while auditing that same file.

**Scope of the claim.** Single-turn assessment prompts. This does NOT measure
whether tone drifts over a long autonomous session; nothing here was run for
more than one exchange.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    ## Professional Honesty 🟡
    **Triggers**: assessments, reviews, recommendations, technical claims

    - **No marketing language** ("blazingly fast", "100% secure", "magnificent") and **no fake metrics** — never invent time estimates, percentages, or ratings without evidence.
    - **Critical assessment**: state honest trade-offs; push back on problems respectfully; say "untested"/"MVP"/"needs validation" rather than "production-ready".
    - **No sycophancy** — professional feedback over praise.

</details>

## retired-token-hygiene
*Retired from the core 2026-08-10, verdict **DUPLICATE** (of live tool contracts*

plus a PreToolUse hook) and **NOW-NATIVE**.

Revert-and-rerun, n=2 paired runs on a fixture repo holding a 74,945 B Python file
with one buried bug, a real 8x8 PNG, and a create-then-edit task. Every marker was
identical across all four runs:

| marker | with rule (r1/r2) | without rule (r1/r2) |
|---|---|---|
| used Write (not a heredoc) for the new script | yes / yes | yes / yes |
| any `cat`/`tee >file <<EOF` in Bash | no / no | no / no |
| read the 74 KB file with `offset`/`limit` | yes / yes | yes / yes |
| read the 74 KB file WHOLE | no / no | no / no |
| Read the `.png` as an image | no / no | no / no |

Each bullet also has a deterministic backstop that the prose only restated:

- **Write over heredoc-to-file** — `check_heredoc_to_file` in
  `scripts/claude-hooks/guard_core.py` DENIES it. Measured against the live
  `~/.claude/hooks/bash-guard.py` by piping PreToolUse JSON into it: a ~215 B
  body ALLOW; ~507 B and every larger size DENY, with the rationale ("the
  heredoc body costs tokens twice") carried in the deny message itself.
- **Read before Edit** — the Edit tool's own contract: "You must Read the file in
  this conversation before editing, or the call will fail." The rule described a
  constraint the harness already enforces by erroring.
- **Read large files surgically** — the Read tool's own contract: "When you
  already know which part of the file you need, only read that part."

**Not exercised by the probe:** "Don't re-read what's already in context". It is
retired with the section on the duplication argument, not on a measurement.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    ## Token & Tool Hygiene 🟡
    **Triggers**: writing scripts/files, editing, reading files, repeated operations

    - **Write tool over heredoc-to-file**: create/overwrite files with the Write tool, never `cat >file <<EOF` / `tee file <<EOF`. The heredoc body is paid for twice (the tool call AND the echoed result) and litters /tmp. A PreToolUse hook blocks large ones.
    - **Read before Edit**: a file must be Read in-session before Edit/Write or the call errors and burns a round-trip.
    - **Don't re-read what's already in context** — use context or Edit directly.
    - **Read large files surgically**: use `offset`/`limit`, or Grep/Glob to locate the symbol, instead of full-file reads.
    - **Don't Read binaries**: skip `.png`/`.jpg`/`.pdf`/etc. unless you must see the image.

</details>

## retired-sleep-blocked
*Retired from the core 2026-08-10, verdict **STALE** — the factual premise is*

false — plus **DUPLICATE** of the Bash tool's own contract.

The rule asserted "`sleep N && <cmd>` is **blocked** by the harness". Measured
directly in a live Claude Code 2.1.220 session, at three points on the N axis
because one measurement is not a general claim:

    sleep 2 && echo "sleep-then-cmd RAN"        -> RAN
    sleep 3; echo "bare foreground sleep RAN"   -> RAN
    date +%s; sleep 15 && echo RAN; date +%s    -> RAN; 1786415522 -> 1786415537,
                                                   i.e. 15 s of real wall time

Nothing was blocked at any of them. The *advice* half — use `Monitor` with an
until-loop, or `run_in_background`, rather than prepending a sleep to a poll — is
already stated in the Bash tool description ("Foreground `sleep` is blocked; use
Monitor with an until-loop to wait on a condition"), so the core was carrying a
second copy of a live tool contract.

🔴 Worth knowing on its own: **the tool description and the measured behaviour
DISAGREE**, and reality won. If a harness change ever does start blocking it, the
tool description will still claim so and this entry will still be the
measurement — re-run the three lines above rather than trusting either text.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    - **`sleep N && <cmd>` is blocked** by the harness — use the `Monitor` tool with an until-loop, or `run_in_background`. Never prepend `sleep` to a poll.

</details>

## retired-nomatch-glob
*Retired from the core 2026-08-10, verdict **STALE** — closed at the environment*

level, and the rule's stated mechanism is now false in the shell that matters.

The rule read: "zsh aborts on unmatched globs (`no matches found`)". The Bash tool
runs NON-interactive `zsh -c`, which sources `.zshenv`, and `.zshenv` carries
`unsetopt nomatch` (from `programs.zsh.envExtra`, deployed to both hosts by
home-manager). Measured in the shell the agent actually gets, against a pristine
shell as the control:

    zsh -c 'echo A; ls /nonexistent-dir-xyz/*.foo; echo "B rc=$?"'
      A
      ls: cannot access '/nonexistent-dir-xyz/*.foo': No such file or directory
      B rc=2               <- shell did NOT abort; the glob passed through literally

    zsh -c 'setopt | grep -i nomatch'
      nonomatch            <- confirms the option is off in that shell

    zsh -fc 'echo A; ls /nonexistent-dir-xyz/*.foo; echo B'      # pristine, control
      A
      zsh:1: no matches found: /nonexistent-dir-xyz/*.foo        <- the old behaviour
      B

`devrc/CLAUDE.md` already documents the `unsetopt nomatch` fix and says unmatched
globs "pass through literally instead of aborting"; the core was contradicting the
project file.

**NOT covered by this retirement, and unchanged:** an unquoted glob that *does*
match something in the cwd still expands, in every shell. That is ordinary
quoting hygiene, not a zsh trap, and no rule was carrying it.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    - **Quote globs meant literally** — zsh aborts on unmatched globs (`no matches found`); quote patterns and kubectl `custom-columns=...[0]...` values.

</details>

## retired-tool-optimization
*Retired from the core 2026-08-10, verdict **DUPLICATE** of a deterministic hook*

this repo had ALREADY built to replace it — the prose was simply never removed.

`scripts/claude-hooks/search-tool-nudge.py` exists because of a 30-day telemetry
measurement, and reaches this conclusion in its own docstring:

> measured over a 30-day window of activity telemetry, Bash is 71% of all Claude
> tool calls (workbench 31,355 / laptop 6,164) while Grep+Glob together were used
> 50 times — and ZERO times on the laptop. RULES.md's "Tool Optimization" section
> already says "Grep over bash grep, Glob over find"; **that prose rule
> demonstrably does not work.**

Independently reproduced by revert-and-rerun, n=2 paired runs on a
search-plus-two-file-reads task:

| marker | with rule (r1/r2) | without rule (r1/r2) |
|---|---|---|
| used the Grep tool | no / no | no / no |
| used bash `grep`/`find` | yes / yes | yes / yes |
| max tool calls in one message | 1 / 1 | 1 / 1 |

The control arm violated the rule in **4 of 4** runs — including one that opened
"I'll do the grep and the two file reads in parallel" and then issued three
separate single-tool messages. A rule disobeyed with it present is not doing work
by being present.

The "Parallelize" bullet is additionally stated verbatim in the agent system
prompt ("If you intend to call multiple tools and there are no dependencies
between the calls, make all of the independent calls in the same block").

**"Delegate complex multi-step work to subagents" was NOT exercised by the
probe.** It is retired on the duplication argument alone — the Agent tool
description covers when to delegate at length — and is the one bullet here with
no measurement behind its removal.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    ## Tool Optimization 🟢
    **Triggers**: multi-step operations, search, complex tasks

    - **Best tool for the job** (MCP > native > basic): Grep over bash grep, Glob over find, context7 for library docs.
    - **Parallelize** independent operations in one message; batch reads/edits; sequential only for true dependencies.
    - **Delegate** complex multi-step work (>3 steps) to subagents.

</details>

## retired-scope-completeness
*Retired from the core 2026-08-10, verdict **NOW-NATIVE**.*

Revert-and-rerun, n=2 paired runs on an open-ended "write me `parse_duration`"
task — the shape that invites speculative CLI wrappers, config, logging and stubs.

| marker | with rule (r1/r2) | without rule (r1/r2) |
|---|---|---|
| files created | duration.py / duration.py | duration.py / duration.py + pd_check.py |
| duration.py bytes | 1586 / 1108 | 1168 / 1687 |
| TODO / stub / NotImplemented | none / none | none / none |

No speculative features, no partial implementations, no placeholder code in any of
the four runs. The size ranges overlap, so there is no scope signal in either
direction — and in r1 the ABLATED arm produced the *more* minimal implementation:
the control had volunteered week units and negative-sign support nobody asked for.

Two honest caveats:

- One ablated run left a `pd_check.py` verification scratch file behind. That is a
  "Clean up" miss, and "Clean up" lives in **Files, Workspace & Safety**, which was
  NOT ablated — so it is not attributable to this cut. Recorded anyway, because it
  is the only asymmetry observed in the four runs.
- "Finish what you start" is also stated in the agent system prompt ("Complete the
  task fully — don't gold-plate, but don't leave it half-done").

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    ## Scope & Completeness 🟡
    **Triggers**: vague requirements, feature work, code generation

    - **Build ONLY what's asked** — MVP first, no speculative features or enterprise bloat (auth/monitoring/etc. only if requested).
    - **Finish what you start**: no partial features, no TODO comments for core functionality, no mock/stub/placeholder code. Every function works as specified.

</details>

## retired-temporal-awareness
*Retired from the core 2026-08-10, verdict **DUPLICATE** of a deterministic context*

injection present on BOTH harnesses that read this file. This was the only 🔴 in
the batch.

The date is not something the model must remember to look up — every session is
handed it. Measured on both consumers:

- **Claude Code** injects `# currentDate / Today's date is 2026-08-10` into
  session context automatically.
- **opencode** — which reads this file via the generated
  `~/.config/opencode/AGENTS.md` — does the same. Probed with the real binary, and
  with a NON-Claude model, so the result is about the harness rather than about
  Claude:

      $ opencode run "What is today's date? Answer with just the date and where you got it."
      Mon Aug 10 2026 — from `<env>` in the system prompt.

  The running model both read the injected date and named its source, with no rule
  in the question.

Revert-and-rerun, n=2 paired runs (a trailing-12-month / 30-month compliance
window question, which forces the arithmetic to commit to an anchor):

| marker | with rule (r1/r2) | without rule (r1/r2) |
|---|---|---|
| anchored to 2026-08-10 | yes / yes | yes / yes |
| stated the anchor explicitly | yes / yes | yes / yes |

The ablated arm volunteered "I only computed from today because that's the only
anchor available" — the rule's own content, produced without the rule.

🔴 **The restore condition.** This retirement rests entirely on the injection. If a
harness ever stops supplying the date, this is the rule to bring back, and the
one-line `opencode run` probe above is how to detect it.

<details><summary>The rule as it stood in the core, verbatim (indented 4 spaces so the archive's `## ` anchor extractor does not read these headings as anchors — de-indent by 4 to recover the exact bytes).</summary>

    ## Temporal Awareness 🔴
    **Triggers**: date/time references, version checks, "latest" keywords

    - **Verify the current date** from `<env>` before any temporal claim; never default to the knowledge cutoff. State the source. Base all time math on the verified date.

</details>

## screen-theft

*Supports: The Operator's Screen Is Not Yours To Take.*

**2026-08-19 — the run that produced the rule.** One capture subagent issued **42
`i3-msg` calls and 14 workspace switches** during a single run and restored
nothing. The calls were re-implementing a raise the tooling already performs:
`browser activate` runs the host-side raise itself, and the skill the subagent
was "helping" contains **zero** `i3-msg` invocations of its own.

**The earlier occurrence, and why prose did not hold.** Browser-bridge telemetry
caught a previous session grabbing the screen **1–5 times per minute** while the
operator was working. That episode was diagnosed at the time as *"partly
self-inflicted by our own docs"*. The skill's Boundaries list then carried ten
prohibitions and none about the operator's screen — the absence read as
permission next to its present siblings, which is why the rule now states the
prohibition explicitly rather than relying on the general principle.

**The axis that actually got taken was the WORKSPACE, not focus.** Every
pre-existing hint in the setup said "restore focus"; none mentioned workspaces.

## guards-narrower

*Supports: A guard's DESCRIPTION is a claim about its COVERAGE.*

Six instances surfaced across three independent audits of two PRs in one session,
and **not one was a logic bug** — every one was a guard or a claim whose wording
was wider than what it actually did.

1. **The fictional two-way pin.** `server.py` said a tier vocabulary was "pinned
   two-way against the CLI by tests/test_browser_session_id.py". That symbol
   appeared nowhere in that file: each side was pinned against its own retyped
   literal and nothing compared them. A mutant that grew the CLI vocabulary *and*
   its own literal, leaving the server untouched, **survived 400 passed / 0 failed**.
2. **The ledger that could not see its own field.** `test_i3_foreground_state_
   vocabulary_is_closed` promised `result.data.i3` "keeps EXACTLY the three values
   consumers branch on" and that a new state "fails here" — but it enumerated a
   *different function's* returns, so a fourth value landed and the test stayed green.
3. **The migrating badge.** A `LIVE-VERIFIED` paragraph describing the `wake` rig
   ended up directly under a new heading about the focus gate, because 136 lines
   were inserted above it — asserting a live verification of a path nobody had run.
4. **Comments naming consumers that do not consume.** Shipped source and README said
   `adoption-scan` and the deadman "read this column". Neither does: adoption-scan's
   browser-bridge entry is `via="source"` and its query never selects `session`;
   `deadman.py` never references it.
5. **The query that returns nothing, forever.** A README's flagship example joined a
   Claude uuid against `ses_`-prefixed opencode ids — measured overlap zero — and
   failed as a silent empty result, the exact mode the surrounding prose argued against.
6. **The regex blind at end-of-sentence.** A version-pin scanner's `(?![\d.])`
   lookahead could not match a version terminating a sentence, hiding **five** stale
   claims across four files — inside the very surface it existed to police.

The common shape: the sentence describes a relationship; the code inspects one side.
Each read as coverage while providing none, which is what makes them worse than an
absent guard — a declared guard stops anyone looking.
