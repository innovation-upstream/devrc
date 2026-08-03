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

**Memory / Failure Investigation**
- [stale-observation](#stale-observation)
- [empty-result](#empty-result)

**Green Test Suite**
- [merged-tree](#merged-tree)
- [two-tiers](#two-tiers)
- [declarations-vs-instances](#declarations-vs-instances)

**Git Workflow**
- [wrong-branch-writes](#wrong-branch-writes)
- [stranded-docs](#stranded-docs)
- [stash-incidents](#stash-incidents)
- [sops-retraction](#sops-retraction) 🔴 **RETRACTED THEORY — do not re-derive**
- [worktree-envrc](#worktree-envrc)
- [base-clone-drift](#base-clone-drift)

**Shell & Tooling**
- [readlink-arbiter](#readlink-arbiter)

---

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
