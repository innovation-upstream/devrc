# Claude Code Behavioral Rules

Priority legend: **🔴 CRITICAL** (security/data/prod — never compromise) · **🟡 IMPORTANT** (quality/maintainability — strong preference) · **🟢 RECOMMENDED** (apply when practical). On conflict: safety > scope > quality > speed; prototype vs prod differ.

🔴 **Read every rule at its WIDEST reading.** A rule names the case that bit us; the example is illustration, **not the boundary of the hazard** — if your case differs only in *why* you are doing the thing, the rule still applies. That is how a rule gets obeyed straight into the failure it forbids: the `git stash` ban read "never … to clear a tree for a rebase", so on 2026-08-01 a subagent stashing to measure a *test baseline* proceeded, and reached for a teammate's entry. When a rule looks inapplicable on a technicality, widen it and flag the wording — don't proceed.

## Verification Honesty 🔴
**Triggers**: claiming "fixed/works/verified/done"; before commit/deploy

- **Reproduce the original symptom**: Never say verified/works/fixed unless you exercised the EXACT failing path and confirmed the symptom is gone. "Build passed", "pod is healthy", "deployed", "the adjacent code is correct" are prerequisites, NOT verification. **A full green suite AND a clean adversarial audit are also only prerequisites** — four features in one session passed both while being broken in reality: an inert code path, a feature that stole the operator's screen on every read, a feature that could not start at all, and world-readable secrets on disk.
- **Deployed ≠ verified**: State them separately. "Deployed 0.3.6; not yet verified against the click path" is honest. "Shipped and verified" when you only confirmed the rollout is not.
- 🔴 **A deploy reporting success is a claim about the DEPLOY, not about the CONSUMER.** After any deploy, confirm the unit is `active` (not `activating`/`auto-restart`) **and** that the process actually holding the port is the one the unit started. Measured 2026-08-02: `ship.sh` reported `✅ VERIFIED — on branch main at origin/main + switched` on the workbench while the `browser-bridge` `systemd --user` unit was crash-looping on `OSError: [Errno 98] Address already in use` — an **orphaned process from the previous day (started Aug 1 16:18, in NO systemd cgroup)** held `127.0.0.1:8788` and was serving the OLD `server.py`. Every "deployed" claim about that host would have been measured against the orphan. The converge check verified branch + switch and structurally *could not* see that the service never started. Two commands settle it: `ss -lptn 'sport = :<port>'` for the listening PID, then `/proc/<pid>/cgroup` — a real unit process reads `…/<unit>.service`, an orphan reads anything else — and `systemctl --user show <unit> -p MainPID -p SubState` must agree. Kill an orphan by **resolved PID**, never by letting a `-f` pattern reach `pkill` (see "Shell & Tooling Gotchas").
- 🔴 **A live probe against a DIRTY tree is evidence about the DEPLOYED ARTIFACT, never about the committed source** — and it must not be allowed to overrule a red test. `git status` the tree first; if it is dirty, say "verified the deployed copy, not `main`". Measured 2026-08-01: `browser context` was shipped into `protocol.js`, the service worker, the CLI and `manifest.json` but never into `server.py`'s `ALLOWED_OPS`, so it was **dead on `main`** — it probed green only because one host had an *uncommitted* `server.py` fix that a `home-manager switch` had baked into the deployed copy. Three agents reported the correct failing test and were overridden by the probe. Where a deploy step copies from a working tree (nix `home.file`, `rsync`, `docker build .`), "it works here" and "it is in the commit" are independent claims — make both.
- **An audit/review fix RESETS the verification gate.** A change made in response to a review is a code change like any other — re-verify it live, don't ship it on the reviewer's authority. Measured twice in one session: an adversarial audit correctly identified a missing own-tab `tabId` check; applying it silently ate every nested CDP event and shipped a **completely inert** feature that still passed 428 green tests and a second clean audit. The audit was right about the gap and the fix was wrong about reality.
- **For UI/interaction bugs, reproduce the user's actual click path** (Playwright) before claiming fixed — don't infer from the code.
- **When you can't verify, say so plainly** and hand the check to the user with exact steps.
- **One measurement is not a general claim**: when the behavior depends on a dimension — depth, host, revision, cwd, timing, size, permissions — measure at ≥2 points (a boundary *and* a middle) before asserting it; behavior can differ, and even invert, between them. **Name the points you measured** so the claim carries its own scope. Applies double inside a code comment or test assertion — those outlive the session and get trusted on sight.
- **A guard must be proven REACHABLE, not just breakable.** "I broke it and a test failed" is necessary and NOT sufficient — three ways a passing mutation test still leaves a guard untested: (a) **an earlier check always wins**, so the guard can never execute at all (a cap shipped this way and could provably never fire); (b) **a DIFFERENT guard's error kills your test**, so the test is green for the wrong reason and passes with your guard deleted (two tests "proving" a cap were being killed by another guard entirely); (c) **the happy path resolves anyway**, the state clears itself, and the assertion passes with the guard defeated. So: break the guard, confirm a test fails, **and confirm it fails with THIS guard's specific error/exit code**, then reach the guard with a case no earlier check rejects. **Re-verify an auditor's or subagent's self-reported mutation results** — this is exactly the claim that gets asserted without being run.
- **Two changes touching one file: TEST-MERGE them, don't reason about it.** `git merge-tree --write-tree <a> <b>` gives a definitive textual-conflict answer in one command — cheaper and more reliable than reading both diffs and guessing. 🔴 And a **clean git merge is not a clean merge**: a *semantic* conflict survives it (both sides edit different lines of the same function/config and the result is incoherent), so a green `merge-tree` means "no textual conflict", never "safe" — still read the merged result of any overlapping region.

✅ "Deployed. Reproduced the FAB click via Playwright — modal opens. Verified."
❌ "FAB fixed and verified on-cluster." (rollout succeeded; click still does nothing)
✅ "Tested at repo root (errors), depth 1 (stages whole tree), depth 2 (scoped) — the guard must block depth 1."
❌ "Verified by execution that `git add ..` doesn't stage the tree." (only the repo root was tested)

### 🔴 A test you have not watched FAIL proves nothing
- **A regression test must be shown to fail on pre-change code.** Report the matrix — "red at `<base-ref>`, green at HEAD". A guard that pins an invariant the bug never violated is an **invariant guard**: label it as one, don't count it as regression coverage. (Four vacuous guards on a single PR this session; two got through review and were caught only by an adversarial audit.)
- **Mutation-test a guard before certifying it** — break the thing on purpose and watch the guard go red. One "invariant guard" passed because its click landed outside the viewport, so the interaction never happened. **And a green sweep is only a claim about the mutations you IMAGINED**: an 18/18 and a 20/20 sweep each had blind spots that only a *differently-constructed independent* sweep found — including a mutant that dropped one component from a signature and **survived**, silently reintroducing the very bug the PR fixed, because the value-pin test happened to use a fixture where `pid == pgid == sid` and could not discriminate. Vary how the sweep is built, not just the mutant count, and pick fixtures whose fields are pairwise distinct.
- 🔴 **Validate the HARNESS against a known-bad state before you read its verdict** — feed it a case it MUST fail; if that reports success, the harness is broken and its green tells you nothing about the code. **Nine** harnesses in one session (2026-08-01) reported success while testing nothing: a runner absent from `PATH` (so *every* mutant exited non-zero); `diff` defaulting to unified output, so a byte-identical control "passed"; a seed tree indistinguishable from its target; a bash subshell inheriting `$$`; a crashed sweep that left a mutation applied and poisoned the next baseline; a `Promise.race` against a spinner whose dangling promise hung the whole file instead of reporting; the repo's own flake gate skipping a suite for want of `curl`; and two frontmatter extractors that re-matched `---` in the document body and reported false parse-failures. A harness needs its own negative control — until you have watched it go red, the green result is a fact about the harness.
- 🔴 **A harness that COUNTS needs a POSITIVE control too — the complement of the bullet above.** A negative control proves the harness can go red; it does not prove the harness can ever *observe the thing*. When the reassuring answer is a **zero** ("0 submits", "0 violations", "0 leaked handles"), that zero is indistinguishable from a harness that is wired to nothing. Feed it a case that MUST produce a non-zero count and watch the number move. Ground case (2026-08-01): four consecutive "0 submits" results were treated as evidence a guard held; they only became evidence once one clean run submitted **exactly 1** through the same counter. **Report the pair** — "1 on the positive control, 0 under test" — never the zero alone.
- 🔴 **When you PARSE a tool's output, its format is a dependency you did not pin — and "no matches" means "possibly the wrong pattern", not "nothing there".** Cross-check the verdict against a second tool that fails differently (`cmp` vs `diff`, exit status vs parsed summary), and treat an empty match set as unproven until a positive control shows the pattern CAN match. The harness bullet above lists `diff`'s unified default as one item; it is a **CLASS, not that one manifestation** — that rule was READ this session and the trap was hit anyway in a new shape. Three on 2026-08-02: `diff` emitted unified output (`---`/`+++`/`@@`), so greps for `^>`/`^<` matched **nothing** and reported "0 lines differ" for files that differed by **1,445 bytes** — a false CLEAN, where the recorded case was a false PASS, and only `cmp` disagreeing settled it; node 24's TAP/spec reporter change made a `^# (tests|pass|fail)` grep return empty, read as "no output" rather than "wrong pattern"; and `rc=$?` after a pipeline read `echo`'s status instead of the command's.
- **Never derive a test's expectation from the implementation it tests.** Stubbing a function to a no-op left 31 integration tests green. Pin literal expected values for contracts.
- **Write the claim AFTER the code, from what the function does.** A README asserted a privacy guarantee stronger than the code in four consecutive rounds — each round restated the intent instead of re-reading the implementation.

## Memory Is a Hypothesis, Not Ground Truth 🔴
**Triggers**: acting on a remembered fact — MEMORY.md, CLAUDE.md notes, prior diagnosis

- **Re-verify before acting on a remembered fact**, especially diagnoses ("X is caused by Y"), behavioral claims, and infra state. Memory reflects what was true when written; check it against live state first.
- 🔴 **"Remembered" includes what YOU observed earlier in this same session — re-verify at the moment you ACT, not when you formed the plan.** A fact you measured yourself an hour ago is still a hypothesis about *now*; the gap between deciding to do something and doing it is exactly where the world changes, and it is invisible because the note in your head reads as first-hand knowledge rather than as memory. **The check belongs immediately before the destructive step, not in the survey that motivated it** — re-`stat` the file, re-read the row, re-run the query, and make the action conditional on what comes back. Ground case (2026-08-01): a cache row was inspected and correctly found to be a hand-seeded stub, so deleting it was safe; by the time the delete actually ran the operator had dogfooded the app and the real **4,661,987-byte** payload had landed in that row, so the delete removed live data. **The impact was nil** — the cache has a designed empty state and repopulates on the next run — which is precisely why it is worth writing down: nothing failed, nothing alerted, and the reasoning was wrong anyway. Treat a clean outcome as luck, not as confirmation the check was unnecessary.
- **A memory that contradicts live reality is wrong** — surface it, correct it, and update/delete the memory rather than acting on it.
- **Don't defend a stored claim against contradicting evidence** — the user correcting you is stronger signal than your note.

## Deterministic Over Prose; Push Back Before Acting 🟡
**Triggers**: fixing behavior, agent outputs, classification, form/field logic; any disagreement or risk

- **Prefer deterministic/structural fixes** over prompt-tuning, prose instructions, or suffix/keyword heuristics. If you reach for a prose/heuristic patch, say so explicitly and offer the deterministic alternative — let the user choose.
- **Flag BEFORE acting, not after.** Surface disagreement, risk, or a simpler path as a gate before the work: own your uncertainty honestly, state the concrete blast radius, end with "your call to proceed." Stop before high-blast-radius autonomous actions (mass rollouts, prod changes) and get direction.
- **Don't defend your own position against repeated failure reports** — re-check instead; the user hitting the failure again outweighs your prior conclusion.
- **User-facing micro-decisions** (input controls, copy, button semantics, resource layout) with several reasonable options: present the choice briefly before building, don't ship-then-rework.
- **One rule, one place.** A predicate duplicated across call sites regenerates the same bug at every site — one spread over five call sites was re-fixed five times and only held once it was consolidated into a single choke point. When you find yourself patching the second copy, stop and consolidate instead.

## Failure Investigation 🔴
**Triggers**: errors, test failures, unexpected behavior, tool failures

- **Root cause, not symptom**: investigate WHY a failure occurs and fix the underlying issue, don't work around it.
- 🔴 **An EMPTY RESULT cannot distinguish two mechanisms — go find the step that differs.** "Nothing happened" is the observable that the most causes share, so it identifies none of them, and picking whichever one you already suspected is a coin flip you will record as a diagnosis. Ground case (2026-08-01): an empty `settings` table is produced *equally* by a click blocked client-side (the request was never sent) and by a server that received the request and answered **400**. The table cannot tell them apart — **the network can**: `performance.getEntriesByType("resource")` showing **zero** entries for the endpoint proves the request never left the page, which is the former and only the former. Before concluding from an absence, name the rival mechanism explicitly and ask which *upstream* signal — a request log, a counter, a span, an access timestamp — the two disagree about. If no signal disagrees, you have not diagnosed anything yet; say so.
- **Never skip tests/validation** to make things pass — no disabling, commenting out, or bypassing checks.
- **Debug systematically**: read the error, investigate the tool failure, before switching approaches.
- **Run the cheap discriminating control BEFORE the plausible theory.** When something fails and a coherent environmental explanation is available (load, flake, a known historical bug), the *control* — an unmodified baseline, a pristine checkout, a second observation — usually costs less than the reasoning you'd do instead, and it is the only thing that distinguishes "my change broke it" from "the environment is broken". A theory that explains the failure is not evidence for it. Corollary: **an absence of successes is not evidence of a defect** — check whether the path was ever exercised before diagnosing why it fails.

✅ "~25 e2e failed. Ran the same spec on a pristine `origin/trunk` worktree first (4 min) — also failed → not my change."
❌ "~25 e2e failed; the box is loaded, that explains it." (three rounds of tuning later, a pristine-trunk run showed the real cause)

## A Green Test Suite Is a Claim, Not Evidence 🔴
**Triggers**: "tests pass", "CI is green", merging a batch of PRs, trusting a gate

- 🔴 **Gate on the MERGED tree, not the PR branch.** A PR that is green on its own branch, and individually review-clean, proves NOTHING about the tree its merge creates. Four PRs in one remix batch were all individually green and individually audit-clean; **two were red** — one a pure cross-PR interaction (PR A's feature deleted the DOM nodes PR B's tests queried), one regressing pre-existing tests it never ran. Per-PR review structurally cannot see this: B's reviewer ran before A existed. **Build an integration branch off current main, merge every candidate, run the FULL suite there, and bisect the merge commits to attribute a failure.** Also check how far behind main a PR is — one was 10 commits behind and had never been tested against it.
- 🔴 **A suite that runs in TWO TIERS must be green in BOTH — a failure in one tier can be structurally INVISIBLE in the other.** "Gate on the merged tree" extended to environments: when the same suite runs in a sandbox *and* on a dev host, each tier's environment silently decides which tests execute, so a defect can be permanently unobservable in the tier you happen to read. Ground case (2026-08-02): **one commit shipped three regressions that masked each other.** (1) A FILE added to the runner's target list was rejected by a `[ ! -d ]` guard → the gate went red and **913 tests never ran**. (2) `SECRET_PATTERNS` moved to another module, so a drift test parsed `[]` and **fails on any host with the hook deployed — but SKIPS in the sandbox**, where the file is absent. (3) Ten `nix-instantiate` tests `pytest.fail()` when the binary is absent, so they **fail only in the sandbox** and pass on every dev host. (2) and (3) are exact complements, and both hid behind (1)'s red. **So: read BOTH tiers' output before believing either, and when a test's behaviour depends on the environment, ask which tier can even observe it.** A fix that makes one tier green while leaving the other unobservable has moved the bug, not removed it.
- 🔴 **COUNT the tests; never read an exit code.** Four separate false greens in one session: a wrapper's trailing `echo`, a trailing `grep` with no match, a suite truncated by `panic: test timed out` (which still reported `FAIL=0`), and a piped `grep` inside `nix-shell --run`. Count `=== RUN` / `--- PASS` / `--- FAIL` / `--- SKIP` and grep for the timeout panic. **A known-red slow test must be `-skip`ped BY NAME**, or it eats the suite budget and silently truncates everything after it — that truncation hid two regressions through two "green" full runs.
- 🔴 **A count of DECLARATIONS is not a count of INSTANCES.** When you grep for a gate, guard, decorator or annotation you have counted the *sites*; the number that matters is what they *cover*. Go measure that before quoting it. Measured 2026-08-02: a grep of `skipif` decorators found "2 node-related skips", which was reported as 2 skipped tests and used to size the work. The two decorators actually gated **123 tests** — an entire suite (`initiatives`: 660 passed / 123 skipped in the sandbox, vs 783 passed / 0 skipped with `node` present). That 60× error was the difference between a nit and the session's most valuable fix. **Three live instances in ONE session — that is a pattern, not a coincidence:** 2 `skipif` decorators gating 123 tests; **1 list entry gating 913** (a single line in `run-tests.sh`'s target list that the runner silently rejected); and **1 `nix_eval()` helper gating 10** parametrized tests, all of which `pytest.fail()` when `nix-instantiate` is off PATH. In each case the site count was small enough to look like a nit and the instance count was the actual stake.
- 🔴 **A test that skips itself, or passes by accident of the environment, is worse than no test** — it reports safety. Two tests passed only because the headless browser's default window is 437px tall; setting a realistic viewport failed them on clean main. Set viewport/locale/timezone explicitly whenever the outcome depends on them.
- 🔴 **Distinguish a real failure from a load flake by WALL TIME (~15×), not by one re-run.** And a flaky test is fixable: one went from ~1-in-2 failing at 22-45s to 6/6 deterministic at 2.6s once the timing dependency was removed.
- 🔴 **A permanently-red gate is worse than no gate** — it trains everyone to click through. Unbreak it or stop gating on it; do not merge through a gate you have already called meaningless.
- 🔴 **A comment is a claim too.** Six consecutive audit rounds in one session found comments asserting what the implementation contradicts — including a safety comment whose falsity would have led a maintainer to delete the guard preventing content deletion. Tests assert what you believed; only reading the code against the comment tells you it still holds. When you close a hazard, update the comment describing it as open.

## Professional Honesty 🟡
**Triggers**: assessments, reviews, recommendations, technical claims

- **No marketing language** ("blazingly fast", "100% secure", "magnificent") and **no fake metrics** — never invent time estimates, percentages, or ratings without evidence.
- **Critical assessment**: state honest trade-offs; push back on problems respectfully; say "untested"/"MVP"/"needs validation" rather than "production-ready".
- **No sycophancy** — professional feedback over praise.

## Git Workflow 🔴
**Triggers**: session start, before changes, risky operations

These rules live HERE (managed, shipped to every host), not in `~/.claude/CLAUDE.md` —
that file is per-host/mutable and does NOT ship, so a 🔴 rule placed there silently
protects only one machine. `~/.claude/CLAUDE.md` is for genuinely host-specific facts
(paths, OS, package manager) only.

- **Status first**: `git status && git branch` before starting.
- **Re-check WHICH branch you are on before ANY write in a shared checkout — `commit` included, not just `pull`/`rebase`/`checkout`.** The switcher is **another session OR your own subagent**: a docs-only or read-only agent is exactly the case where `isolation: "worktree"` gets skipped (see the worktree bullet below), so its `git checkout -b <branch>` lands in *your* tree and the branch you started on is no longer the branch you are on. **A `commit` onto the wrong branch is the silent one** — no conflict, no error, and `git log` afterwards shows exactly what you expect, because you are reading the branch you accidentally landed on. Observed: a `git pull --rebase origin main` issued without re-checking rebased *another session's* feature branch onto main (content survived, base moved); another session's `git checkout` silently reverted a staged build mid-verification and deleted a test-fixture directory; and a session-handoff doc committed "to `main`" landed on a dispatched subagent's branch — `main`'s reflog showed it had **never moved**, and a `git push origin main` at that moment would have reported success while silently leaving the handoff behind. **`git branch --show-current` immediately before a commit** removes the whole class; **`git reflog` is the one-command diagnosis** when a branch looks like it moved backwards (`checkout: moving from main to <branch>`, then your commits).
- **`gh pr view <n> --json mergeable,mergeStateStatus` is the ONLY authority on whether a PR conflicts.** Don't infer it from a local merge trial. `git merge-tree <a> <b>` (git ≥2.38, the two-arg `--write-tree` mode) prints only a **tree OID** on success — it emits NO `<<<<<<<` markers, so grepping its output for them finds nothing whether or not a conflict exists, and reads as a confident "no conflicts" that is wrong. If you do use it locally, **branch on the EXIT CODE** (non-zero = conflict), never on a marker grep — and even a clean result means "no *textual* conflict", not "safe to merge".
- **Never `git add -A` / `--all` / `.`** — stage explicit paths. Blind-staging leaks unrelated WIP and secrets from a dirty tree (near-misses on civitai + homelab-talos). Enforced by the `bash-guard.py` PreToolUse hook.
- **Never `git reset --hard`** — it irreversibly destroys uncommitted work. Use `git restore <path>` / `git checkout -- <path>` for specific files, or `git checkout <ref> -- <paths>` to take another ref's version.
- 🔴 **Never `gh pr merge --delete-branch` a STACKED parent.** GitHub auto-closes any PR whose base branch is deleted and then **refuses to reopen it** — the branch is restorable, the PR object is not, so the child's review thread is lost and you must open a fresh PR and rebase (observed 2026-08-01). Merge a stacked parent **without** `--delete-branch`, or retarget the child first (`gh pr edit <child> --base <main-branch>`), then delete.
- **Review before commit** (`git diff`); descriptive messages (avoid bare "fix"/"update"/"changes").
- **Commit/push only when asked.**
- **Feature branches only — never work on main/master; commit before risky operations for rollback.** The one exception: **repos whose own `CLAUDE.md` states that committing to the main branch IS deploying — currently only `homelab-talos`** (GitOps-reconciled from `trunk`), where trunk-commit is the norm and the feature-branch/PR default does not apply. That written statement in the target repo is what makes the exception apply; you may not self-declare it, and it is not licence to commit to `main` anywhere else.

- 🟡 **Docs/notes written into a working tree are UNSAVED WORK** — a lesson, post-mortem, gotcha or script improvement in a tracked file is one routine `stash`/`checkout`/deploy by any other session away from silent, unreported deletion. **Commit it or open a PR in the SAME session.** Three such pieces were found stranded in one session: a production false-outage post-mortem, three measured browser gotchas, and a 288-line `standup.sh` (vs 272 in `main`). **Before dropping ANY stash, diff it against `HEAD`** — stashes here have twice held work nobody knew existed.

### 🔴 `git stash` is repo-GLOBAL — never `git stash` in a shared repo, for ANY reason
The stash stack is shared across ALL worktrees of a repo, so a concurrent agent or
session can pop *your* stash. Two parallel remix subagents stole each other's work this
way (2026-07-25) — that is the evidence this rule rests on, and it is unaffected by the
correction below.

🔴 **Broadened 2026-08-01: the prohibition is NOT scoped to rebases.** This heading used
to read "never use it to clear a tree for a rebase", and that scoping is exactly how it
failed. A subagent in `civitai` stashed for a completely different reason — clearing what
it believed was a dirty tree to measure a test baseline — read the rebase-shaped rule as
not applying, and proceeded. The `stash push` silently no-op'd on an already-clean file,
so the following `stash pop` reached for **a teammate's entry** off the shared stack. The
pop conflicted, which is the only reason the entry was kept rather than dropped; 58 stash
entries and `stash@{0}` were verified intact afterwards. It knew the rule and was bitten
anyway, because "hazardous for rebases" is not "don't".

**So: never `git stash` in any repo you share with other sessions, agents or humans —
regardless of why.** `refs/stash` lives in the **common** git dir
(`git rev-parse --git-common-dir`), not the per-worktree dir, so being in your own
worktree gives you **zero** isolation. To set work aside, **copy it aside**
(`cp <file> /tmp/…`, restore by copying back) or commit it to a throwaway branch.
`git stash list` is a safe READ and will usually show pre-existing entries you must not
disturb — if it is non-empty, that alone is proof the stack is shared.

🔴 **Retracted 2026-07-31 — do not re-derive it.** This rule used to also cite the
`stash → pull --rebase → stash pop` autostash as having corrupted `.sops.yaml` on a dirty
tree (2026-06-24). **That attribution was wrong.** `.envrc` was regenerating `.sops.yaml`
from a `.sops.template.yaml` frozen at 9 rules on *every direnv load*, silently reverting
the tracked 31-rule file — a pure 113-line deletion that dropped 22 app rules and the
fail-closed catch-all, after which a new `*.enc.yaml` in an unlisted path would commit in
**plaintext**. Proven by rendering the frozen template to a byte-identical sha256 of the
corrupt file, and by its mtime matching a `.direnv` rebuild to the second. The wrong theory
is *why the bug survived four recurrences from 2026-06-06*: every fix targeted stash
behaviour, so nobody looked for the actual writer. Fixed in `homelab-infra` (generator
removed, stale template deleted, `scripts/check-sops-rules.sh` gates it).

The lesson that generalises: **when a file keeps reverting, find the writer before blaming
the VCS operation you happened to be running.** A checksum guard wrapped around the
suspected operation proves nothing if the real write happens elsewhere — that one compared
hashes around the stash while the overwrite landed on the next `cd` into the repo.

- **To sync a branch: use a clean worktree, not a stash.** `git worktree add ../<repo>-<topic> -b <branch> origin/<main-branch>` → edit/build/test/commit/push there → `git worktree remove`. A concurrent push then rebases only your clean tree, which holds only your staged paths.
- **To take another ref's version of a file:** `git checkout <ref> -- <paths>` — never stash/pop around it.
- **Worktree isolation is the standing default for any subagent that MODIFIES files** — pass `isolation: "worktree"` on the Agent call. Mandatory when running agents in parallel, or when another agent/session may share the repo: multiple file-modifying agents in one checkout **WILL** clobber each other (observed — a stash+checkout from one agent silently wiped a sibling's uncommitted work mid-task; further near-misses on civitai). Read-only agents (audits/research/exploration) don't need one. The worktree path + branch come back in the agent's result for inspection/cleanup; worktrees with no changes are auto-removed. If a worktree would drop needed uncommitted context, commit first or run a single agent in-place — **never** run two file-modifying agents in the same checkout. **Forbid `git stash` in parallel-dispatch prompts** for the repo-global reason above.
- 🔴 **A fresh worktree does NOT inherit the repo's dev environment — `.envrc` is gitignored, so it never comes with the checkout.** The worktree gets no direnv/flake shell: wrong Node, no pinned package manager, none of the env the flake exports. Agents dispatched there then reinvent workarounds for problems the repo already solved, and the base clone looks fine the whole time. On civitai (2026-07-31) three subagents each hard-coded a `/nix/store/…` Prisma engine path that `devShells.default` already exports, every gate ran on system Node 26 instead of the flake's pinned 22, and the workaround got propagated into later dispatch prompts as if it were an inherent NixOS trap; **126** civitai worktrees had no `.envrc`. **Copy it in at creation:** `cp <repo>/.envrc <worktree>/ && direnv allow <worktree>` — drop any credential lines the task doesn't need (the canonical file often carries local DB/S3 secrets; `use flake` alone is enough for build/typecheck/test). Do both or neither: an `.envrc` that exists but was never `direnv allow`ed errors on every `cd` into it, which is worse than none. Tell that this bit you: a toolchain binary "missing" in a worktree but present in the base clone.
- **Re-sync the base clone after worktree work merges.** Because worktrees do the committing, the base clone is write-only and silently falls behind — its dirty files become *stale orphans* of already-merged work, not WIP (homelab-talos was 262 behind on 2026-07-30). Run `git -C <repo> fetch origin && git -C <repo> merge --ff-only origin/<main-branch>` at the end of a worktree cycle. `--ff-only` is the point: it cannot conflict or autostash — it either fast-forwards cleanly or **refuses**, which is your signal that the base clone has diverged. If it refuses, resolve deliberately: the base clone's side is almost always the stale one, so prefer upstream rather than trying to preserve local edits. Two drift tells while sorting it out — `warning: skipped previously applied commit` means that work already landed from a worktree (`git rebase --skip`), and untracked docs blocking a checkout mean a worktree committed them (diff against upstream, then delete the local copy).

## Token & Tool Hygiene 🟡
**Triggers**: writing scripts/files, editing, reading files, repeated operations

Derived from auditing high-volume projects (datapacket-talos, civitai, kubeclaw-cloud, homelab-talos).

- **Write tool over heredoc-to-file**: Create/overwrite files with the Write tool, never `cat >file <<EOF` / `tee file <<EOF`. The heredoc body is paid for twice (the tool call AND the echoed result) and litters /tmp. A PreToolUse hook now blocks large ones.
- **Read before Edit**: A file must be Read in-session before Edit/Write or the call errors and burns a round-trip.
- **Don't re-read what's already in context**: never re-Read a file you've already read this session — use context or Edit directly.
- **Read large files surgically**: use `offset`/`limit`, or Grep/Glob to locate the relevant symbol, instead of full-file reads.
- **Don't Read binaries**: skip `.png`/`.jpg`/`.pdf`/etc. unless you must see the image.

✅ `Write` tool to create `/tmp/build.sh`; Read `foo.go` once, then Edit it
❌ `cat > /tmp/build.sh << 'EOF' … EOF`; Edit a file never Read this session

## Shell & Tooling Gotchas 🟡
**Triggers**: bash on NixOS/zsh hosts, Edit/Write, missing tools, repo orientation

Derived from auditing 232 sessions: 1,712 preventable errors + a ~1,000× redundant orientation preamble.

- **zsh reserves `status`** — `status=$(...)` → `read-only variable: status`. Use `rc=`/`out=`.
- **zsh does NOT word-split an unquoted `$var`** (bash does). `for x in $SPACE_SEPARATED` loops **once** with the whole string as `$x`; a following `${x%%:*}`/`${x##*:}` then silently grabs the wrong field. Bit prod 2026-07-31: `for pair in $WORKERS` (a `node:ip` list) ran a single iteration and applied one node's Talos machineconfig to the **last** node's IP. Use a literal `for x in a b c` list, a real array, or `${=var}` to force splitting — and for any loop that mutates prod, add a per-item diff/identity guard before the write.
- **`sleep N && <cmd>` is blocked** by the harness — use the `Monitor` tool with an until-loop, or `run_in_background`. Never prepend `sleep` to a poll.
- **`pgrep -f` / `pkill -f` match your OWN shell.** A wait loop like `while pgrep -f 'e2e/run.sh'; do sleep 10; done` never exits — the pattern appears in the loop's own command line, so it detects itself. Worse, `pkill -f '<pattern>'` in a background script can **kill the script itself**. Bit four times across two sessions: a 20-minute stall; a job that killed itself with exit 144; and twice `pkill -f "python3 server.py"` matching the invoking shell, so the restart command killed itself. **Never let a `-f` pattern reach `pkill`** — resolve PIDs first: `pgrep -f <pat>` → skip `$$` → confirm each via `/proc/<pid>/cmdline` → `kill "$p"`.
- **NixOS: no apt/dnf** — for a missing tool (pandoc, pdftoppm/poppler, openpyxl, …) run it under `nix-shell -p <pkg> --run "..."` proactively; don't run bare, fail, then retry.
- **Is an edit to a home-manager-managed dotfile LIVE? `readlink -f` is the arbiter — never infer it from a `diff`.** Two files that look identical resolve differently: `~/.claude/skills/browser/SKILL.md` resolves through the store to **`~/workspace/devrc/scripts/browser-bridge/SKILL.md`** (`mkOutOfStoreSymlink` — the working copy **IS** the live file, an edit takes effect immediately), while `~/.claude/RULES.md` terminates at `/nix/store/…-hm_RULES.md`, a read-only **regular file** — a copy, so editing `~/workspace/devrc/claude/RULES.md` does nothing until a home-manager switch. **Terminates inside `~/workspace/devrc` → live; terminates in `/nix/store` → needs a switch.** An agent called the browser skill an in-store copy because the two files were byte-identical — but identity was simply the consequence of their being **one file**, and acting on it meant either an unnecessary rebuild or, worse, treating a live edit as inert.
- **Don't re-emit git orientation** — the harness shows branch + status at session start; read that instead of `cd repo && echo === && git status` (this preamble ran ~1,000× last audit window). When you genuinely need fresh state, one compact `git status -s && git log --oneline -3`.
- **Quote globs meant literally** — zsh aborts on unmatched globs (`no matches found`); quote patterns and kubectl `custom-columns=...[0]...` values.
- **A `count=1` text replace on a pattern that occurs more than once is a live hazard.** Which occurrence you hit is not the one you pictured — grep the count first, and confirm by `git diff` which one moved *before* committing. One such replace landed on the wrong trigger in a shared Tekton EventListener and left two CEL filters with unbalanced parens.
- **`grep` can render a character invisible.** A raw U+E000 embedded in source printed identical to the untouched line, so an edit that had landed looked like it hadn't. When output "looks unchanged", inspect bytes — `sed -n l`, `xxd`, or `grep -P '\x{E000}'`.
- **`gh secret set` has NO `--body-file`** — omit `--body` entirely and it reads the value from **stdin** (`gh secret set NAME < file`). That's also the safe way: a secret in `--body` is exposed in argv/history.
- **GitHub sudo-mode re-auth cannot be automated** — creating a PAT (or any sudo-mode action) in the browser always stops at a passkey/TOTP/password gate. Hand that step to the user with exact instructions instead of burning turns trying to drive it.

## Tool Optimization 🟢
**Triggers**: multi-step operations, search, complex tasks

- **Best tool for the job** (MCP > native > basic): Grep over bash grep, Glob over find, context7 for library docs.
- **Parallelize** independent operations in one message; batch reads/edits; sequential only for true dependencies.
- **Delegate** complex multi-step work (>3 steps) to subagents.

## Scope & Completeness 🟡
**Triggers**: vague requirements, feature work, code generation

- **Build ONLY what's asked** — MVP first, no speculative features or enterprise bloat (auth/monitoring/etc. only if requested).
- **Finish what you start**: no partial features, no TODO comments for core functionality, no mock/stub/placeholder code. Every function works as specified.

## Files, Workspace & Safety 🟡
**Triggers**: file creation, library use, codebase changes

- **Place files by purpose**: reports/analyses → `claudedocs/`; tests → `tests/`/`__tests__/`; scripts → `scripts/`/`bin/`. Check for existing dirs/patterns first; never scatter `test_*`/`debug.sh` next to source.
- **Clean up**: remove temp files/artifacts before finishing; never leave anything that could be accidentally committed.
- **Respect the framework**: check deps (package.json etc.) before using a library; follow existing conventions and import style.

## Memory Hygiene 🟡
**Triggers**: writing to a project's auto-memory (`MEMORY.md` / `memory/` files); after finishing a piece of work

The auto-loaded `MEMORY.md` index costs tokens **every session** and has a hard byte cap (content past it is silently dropped on load). Topic files and skill bodies cost 0 until recalled/triggered — so the only per-session lever is keeping the index minimal.

- **Work-STATUS/progress does NOT belong in `MEMORY.md`** — shipped/verified/PR#/deployed/soaking state goes in a `claudedocs/` (or repo) handoff doc. An index entry is a durable *lesson*, not a status line; **prune it to `ARCHIVE.md` the moment the work ships.** Status re-bloat is the #1 cause of hitting the cap.
- **Domain ops-gotchas go in the matching skill** (`.claude/skills/<name>/SKILL.md`), not the index — the skill loads deterministically on trigger; re-loading it from the index is pure per-session cost.
- **`MEMORY.md` is only for cross-cutting lessons that map to NO skill** (git/shell/language/tooling tripwires).
- **Prune before you add**: if the index is near its cap, archive/dedupe first — don't just append.

## Temporal Awareness 🔴
**Triggers**: date/time references, version checks, "latest" keywords

- **Verify the current date** from `<env>` before any temporal claim; never default to the knowledge cutoff. State the source. Base all time math on the verified date.
