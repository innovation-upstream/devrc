---
clawgate-task: 366
---
# Handoff: cairn — 2026-08-27

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
The agent-facing layer of devrc now has a name — **Cairn** — and a direction: keep iterating
locally, then decouple it into something deployable that a **team instance** can read and write.
This doc records what shipped, what the name applies to, and the coupling points that block a
team instance, each measured rather than assumed.

## State now
🔴 **EVERY RANKED ITEM FROM THE PREVIOUS LIST IS CLOSED.** Cards #366, #391 and #394 are all
`complete`. The ranked list below is entirely new work.

- **Branch / PR:** nothing of mine in flight. Merged this session, each verified **by content** on
  the target branch (a squash is never an ancestor):
  - homelab-infra **#474** → `84a69a4b` — the #391 supersede stamp (rank 6)
  - homelab-infra **#489** → `c04f1934` — the #394 comment-scope + ledger guard (rank 7)
  - devrc **#972** → `10643aef`, **#984** → `8a14a556`, **#987** → `69b11f16` — three handoff
    corrections, one per rot (see Gotchas)
- **Deployed AND verified — separate claims.** clawgate **0.8.9** shipped (`abc0bdb2`, both version
  literals), Flux reconciled, pod ready 0 restarts, server reported `0.8.9`. Verified live by a real
  `POST /tasks/merge` on throwaway tasks #412/#413: loser `open ['throwaway']` →
  `complete ['superseded-by:412','throwaway']`, winner untouched and the card's verifier returns
  **rc 1** against it — a negative control, not only a positive one.
- ⚠ **LIVE IS NOW 0.8.11, NOT 0.8.9** — two releases shipped from other sessions after mine. The
  feature survived: stamp code still on trunk, and the **running 0.8.11 binary** carries
  `superseded-by` (1) with positive control `tasks:changed` (3) and a negative control (0). Re-check
  the live pin rather than trusting this line.
- **Rank 3 needed no work from me** — it was already done in open PR #948, which merged
  `2026-08-28T21:11:33Z`. I claimed it, measured, wrote nothing, released the claim.
- **Clean:** no worktrees, branches or claims of mine in either repo. Both devrc hosts converged and
  verified at one sha, cross-host agreement **compared** (not the "NOT COMPARED" gap).
- 🔴 **`~/workspace/devrc` sits on ANOTHER SESSION'S branch** (`feat/flake-lock-and-discord-ext` at
  time of writing) and `homelab-talos` carries pre-existing dirty files. Neither is mine. **Check
  `git branch --show-current` before pointing any committing tool at either.**

## Open investigations — live diagnosis state

### ~~The sanitized export leaks three scope names~~ — DIAGNOSED AND FIXED
🔴 **The leading hypothesis in the previous handoff was WRONG, and acting on it would not
have fixed the leak.** It read: *short, word-like scope names fall below the length ladder;
the fix is a scope allowlist keyed on the store's own scope set.* Measured 2026-08-26:

- **They were not scopes at all.** The store's scope set is `civitai`, `civitai-*`,
  `claude-pool`, `cli`, `datapacket-talos`, `devrc`, `flipt-state`, `homelab-infra`,
  `homelab-talos`, `kubeclaw`, `storage-resolver`. `naida`, `vetr` and `auditloop` are absent,
  so the scope class removed **zero** of the leaked occurrences — the one that *did* vanish in
  each pair (`vetr.com`, `auditloop.zacx.dev`) went via the **host** rule.
- **The length ladder was a red herring.** `auditloop` is 9 chars and would have been matched
  aggressively *if it had been a scope*.
- **The store's scope set was already the substitution source** (`sanitize.build()` →
  `measurements.by_key("index.store")`). It is a curated index, not an inventory of names that
  must not leak.
- **The real class:** all three survivors sat in the `skills.inventory` row — human-authored
  prose harvested out of `claude/skills/*/SKILL.md`. Substitution redacts identifier CLASSES it
  has been shown; a harvested sentence has no class at all.

🔴 **Widening the name list was tried and REJECTED ON MEASUREMENT, not on taste.** Sourcing
names from the index store + the skill directories + all 149 directories under `~/workspace`
still left `naida` **fully intact** — the directory is `naida-ai` and the leak was a bare
`naida` in a sentence — while corrupting ordinary English: `"run the test suite from a scratch
dir"` → `"run the scope-108 suite from a scope-104 dir"`. **A name list is fail-OPEN by
construction**; it closes exactly the names something happened to be called after, and an
entitlement boundary may not be.

**The fix (this PR):** a measurer DECLARES what each column holds
(`Measurement.column_kinds`): `""` ordinary, `"name"` a local identifier substituted *inside
that cell only*, `"prose"` **withheld** in a sanitized build. Withholding is driven by the
declaration, never by a name list, so it still happens on a host where every name source is
empty. An unknown kind is withheld too — the fail-closed direction.

- **Verified** — every client name to 0, with the mechanism proven live, not a bare zero:
  `naida 2→0`, `vetr 4→0`, `auditloop 4→0`; controls `civitai 5→0`, `datapacket 2→0`,
  `kubeclaw 2→0`, `zacx.dev 3→0`; page still renders **37 rows, 37 withheld cells, 77 name
  stand-ins**; ordinary prose uncorrupted (`test` 23 in both builds).
- **Residual, stated honestly:** devrc's *own* subsystem names still appear — in file paths
  (`scripts/repo-cos/tests`), systemd unit names (`repo-cos.timer`) and `content.py`'s static
  narrative, none of which pass through a declared column. That is correct: the page is *about*
  devrc. It would become a leak if a script were ever named after a client.
- 🔴 **Two `SANITIZE DEGRADED` lines still print, and both are honest.**
  *"hostname indistinguishable from a word"* is the nodename; *"scope matched in its exact form
  only"* is the 3-char scope `cli`. Both are deliberate declines, counted in the legend. They
  are **not** the leak and were never related to it.
- **Separately worth a decision (not fixed here):** `claude/skills/*/SKILL.md` is tracked in
  this **public** repo, so those client names are already published in git. The sanitizer is now
  correct; the repo-level exposure is a different question.

### Tekton silently did not fire for one push
- **Symptom + exact repro:** pushed `4b9e692a` to a PR branch; both required checks sat
  `pending` for 45 min with no PipelineRun ever created.
- **Observed:** `kubectl get pipelinerun -n tekton-ci -o jsonpath=…` over all runs matched
  **no** row for that revision, while runs for other shas existed and completed normally. A
  later push of the same tree under a fresh sha ran and passed.
- **Ruled out:** the documented `timeouts.tasks` stall — that leaves a PipelineRun that exists
  and never reports; here nothing was created at all.
- **Leading hypothesis:** a dropped webhook delivery. Unconfirmed.
- **Why it matters:** from GitHub's side this is indistinguishable from a slow run, and both
  checks are required with `enforce_admins: true`, so the PR is unsatisfiable until someone
  reads the cluster. Only a fresh push clears it.
- **Next probe:** check the EventListener pod logs for that delivery id, and whether GitHub's
  webhook delivery log shows a non-2xx for it.

### ~~Six remaining positional audit reads~~ — CLOSED, and the numbers below are the stale ones
🔴 **This block is kept only to show how its own framing rotted — read rank 3, not this.** Every
number here was true when written and every one of them was wrong within a day, because the file
was being grown by a branch nobody reconciled against.

- **What it said:** `scripts/tests/test_subsystem_store_api.py` has **38** audit-list reads;
  **12** make more than one auditable request and **7** read positionally; #882 fixed one of the
  seven, so **six** remain across **11** sites.
- **What was there on 2026-08-28:** **59** subscript lines across **~40** test functions. The
  eliminations aged fine; the population they were counted over did not.
- 🔴 **`await_audit()` guarantees the lines EXIST, not their ORDER** — still true, still the
  reason a conversion must assert a multiset or select by identity, and independently rediscovered
  by the branch that did the work: `92f6650e`'s subject is *"closing the audit COUNT race exposed
  an ORDER race underneath it, and made it likelier"*.
- **Closing condition, and how it was actually met:** the card asked for each of 11 sites shown
  red under a forced `_audit` delay. #948 met it differently and, on balance, better — ONE site
  observed failing for real (`test_a_LOCKED_OUT_response_is_BYTE_IDENTICAL_to_an_ordinary_401`
  read `audit[-1]` and got the previous request's `status=unauthorized`), plus a reachable
  structural ledger that fails when the class regrows. Forty per-site red demonstrations would
  have proven less and rotted faster.
- **Rejected deliberately, and still rejected:** `daemon_threads = False` — measured to work, but
  several tests leave a request unfinished on purpose and an unbounded join in a required check is
  a blind trade.

## Next steps (ranked)
1. **Land `rescue/espanso-workbench-2026-08-28`** — repo `devrc`, one file, `nix/home.nix`. It
   splits the `:acq` espanso trigger into `:dacq` (full dispatch text) and `:acq` (plain "ask
   clarifying questions"). It was committed to the workbench's `main`, blocked `ship.sh` (rc 8), and
   **another session reset `main` at 14:34, dropping it** — it survives only because it was pushed
   to that branch first. Content is NOT on `origin/main` (patch-id compared over 30 days). Needs a
   PR. Closing condition: merged, then `ship.sh` shows both hosts at the new sha.
2. **Sync the laptop's `homelab-talos`** — `drift-check.sh` reports **rc 17**: the two hosts build
   DIFFERENT source for `containers/clawgate` (workbench subtree `68b28b4c`, laptop `c71bc616`), so
   the laptop's `clawgatectl` is built from stale source. Laptop is clean, 0 ahead, **9 behind**, and
   the 9 are other people's commits — **my work is on both hosts**. Fix:
   `git -C ~/workspace/homelab-talos merge --ff-only origin/trunk` on the laptop. Closing condition:
   drift-check stops reporting rc 17.
3. **Assert the supersede chip end-to-end** — `containers/clawgate/e2e/tests/tasks.spec.ts` asserts
   the loser's merge *comment* and the winner's chips, but not the loser's new `superseded-by:` chip.
   The Go tests cover the write; the **visual** half of #366 criterion 3 is unexercised e2e. Repo
   `homelab-talos`. Closing condition: a merged PR touching that spec.
4. **Delete throwaway tasks #412 and #413** — both `complete` and annotated with what they were.
   Board noise only. ⚠ `DELETE /api/tasks/{id}` is `dismissTask` and tears down a live agent pod;
   these have none, but read `task-api.md` before reaching for it.
5. **The Tekton non-fire** (unchanged, see Open investigations) — untouched this session.

## Gotchas / decisions / dead-ends
- 🔴 **TWO SESSIONS CAN BOTH CLAIM CORRECTLY AND STILL COLLIDE — the lock matches SLUGS, and the
  overlap was in the DIFF.** Rank 3 was fully implemented in open PR #948 for ~16 h before anyone
  here looked. The other session claimed `cairn-write-path` for the *write path*; this reader
  derived `cairn-3` for the *test conversion*; neither was wrong and the slugs never met. A slug
  lock structurally cannot look at what a branch actually changes, so `gh pr list --state open`
  found in one command what the lock could not. ⚠ **`design-claim-by-push.md`'s "What is NOT
  covered" list (`:659-670`) has no bullet that fits this exactly** — not "never claimed at all"
  (`:661`, plainly wrong here) and not really "Reworded duplicates" (`:665`, which is about one
  item worded two ways; these were two different items completed by one diff). The gap is worth
  filing there rather than papered over with the nearest label. Corollary for this doc's ranked
  list: **a ranked item can be
  completed by a PR that never names it**, so re-scoping means diffing the file, not reading
  titles.
- 🔴 **A SITE COUNT IN PROSE IS A MEASUREMENT WITH AN EXPIRY DATE.** Rank 3's "six remaining …
  all 11 sites" was 5.3×–7.3× low within a day because a concurrent branch was growing the
  same file.
  Same family as the suite-size literals below, but worse: a suite total reads as trivia, whereas
  a site count reads as *scope* and is what a session budgets against. Re-measure at the moment
  you act; never carry the number forward.
- 🔴 **`test_doc_path_rot` DOES NOT SCAN `claudedocs/` — its `CORPUS_DIRS` is
  `("claude", "CLAUDE.md")`, and its own comment says so.** Four separate reports in this effort
  quoted its green as evidence that THIS doc's paths were checked. They were not: that was a zero
  from a scan which never walked the file — the reassuring-zero failure, committed while writing
  about it. **Before quoting a gate, check what it SCANNED**, and prefer a gate you have watched
  produce a non-zero on a case that must fail.
- 🔴 **A COUNT IS A CLAIM ABOUT THE TOOL THAT PRODUCED IT.** The rank-3 rewrite said
  `scripts/tests/test_subsystem_store_api.py` **at `92f6650e`** was 12,264 lines; `wc -l` says
  **12,262**. (Pin the revision as well as the file: on `origin/main` the same command says
  8,308, so an unpinned count sends the reader to a third number.) Both are honest: it holds one
  **U+2028** and one
  **U+2029**, and Python's `str.splitlines()` treats them as line breaks while `bytes.splitlines()`
  and `wc -l` do not. Same family as grep rendering a character invisible — **when a count matters,
  produce it two ways that fail differently**, and prefer the byte-level one.
- 🔴 **AN OBSERVATION FROM EARLIER IN YOUR OWN SESSION IS STILL A HYPOTHESIS ABOUT NOW.** This
  doc's first draft told the next reader not to run the merged-tree gate because a
  `devrc-integ-948` worktree "already exists". It had existed — it was in a `worktree list` earlier
  the same session — and it was gone hours later. The sentence would have disarmed a gate the same
  paragraph argues is still owed, on a stale first-hand memory that reads exactly like knowledge.
  **Re-check immediately before the step that depends on it, not in the survey that motivated it.**
- 🔴 **#438 took THIRTEEN adversarial rounds, and the shape of what they found is the lesson.**
  Rounds 1-5 found behaviour defects. Rounds 6-13 found almost none — what they found instead
  was **prose claiming coverage it did not provide**, which is the same defect class the tool
  itself exists to catch, reproduced in the artifact describing the tool. Worth reading before
  budgeting an audit ladder: the late rounds were NOT waste, they were where the durable record
  got made honest.
  - **A degenerate combinator hid three unreachable arms.** `max(horizon, evidence_from)` reads
    as a choice, but `evidence_from` is the `min` over per-pipeline oldest runs and the horizon
    is a `max` over a **subset** of those same values — so `horizon >= evidence_from` is a
    theorem. Replaced with an explicit dominance rule and the invariant PINNED by a test, so
    redefining either input fails loudly instead of quietly making dead code live.
  - 🔴 **"Verified in isolation" struck again, and it is the most valuable finding of the run.**
    `--branch` could go **entirely inert** with the whole suite green: each function was pinned,
    and nothing pinned that `main` handed the override to both. The harness was complicit — the
    stub was a kwargs-**swallowing** lambda, so one side of the seam was structurally invisible.
    **A stub that discards what it was given cannot witness what was passed.**
  - 🔴 **A commit message described a fix that never landed.** The sentence it claimed to
    correct was byte-identical across both commits. Worse than the original error: a message is
    the durable record and asserts a tree state a reader can only disprove by diffing.
  - 🔴 **I overturned a CORRECT audit using a "mutant" that was an INSERTION.** The path I
    "mutated" held no expression to mutate — the guard short-circuits before it — so my ternary
    ADDED a branch, killed by crashing rather than by any assertion, and no operator generates
    it. 803 operator-generated mutants: zero unique to the test I was defending. **Ask whether
    your mutant is a perturbation of code that exists.**
  - **Two agent reports were confidently wrong** in ways I would have propagated had I not
    re-measured: one on which test carries unique load, one on a "no coverage gap". Re-run an
    auditor's mutation results; the rule earned its keep twice here.
  - **A positive control can be INERT.** Adding defaults to keyword-only params every caller
    passes mutates nothing, so its "SURVIVED" was a fact about my control, not the harness.
  - **Suite-size literals in prose rot.** "survives all 86 tests" went stale inside one commit.
    Say "the whole suite"; same for absolute totals (687 → 850 → 857 → 886 → 892 in three days).
  - **Detection power was quantified rather than assumed**: 5 of 7 repos have no at-cap
    pipeline, and 3 are effectively silent (bounds hours old). Stated in the tool, the PR body
    and the code — it is the number that decides whether this keeps earning its keep.
- 🔴 **The store-api retirement was proposed and CLOSED by the operator** (devrc #849,
  homelab-infra #404, both `mergedAt=null`). The pivot: clawgate task **#360** makes the hosted
  store the single datastore and ships the missing phase-2 CLI + phase-3 append write path.
  Closing those two PRs is literally #360's criterion 1. **Do not re-propose retirement** — the
  store was unused because the *client* was never built, not because the need was absent.
- **The "must never gain a git remote" rule is bad and is being removed** (operator ruling
  2026-08-25, `claudedocs/proposal-subsystem-store-homelab.md:23`). It is absolute where its own
  stated intent was narrower — no *third party*. Replacement must keep the third-party
  prohibition while permitting a remote on a host you own.
- **Three flakes were fixed by reproducing them first**, not by re-running: `2/50 → 100/100`,
  `1/1 forced red → green`, `4/180 → 0/180` with lock sightings `43/48 → 0`. A flake that merely
  stops failing is not fixed — passing is what a flake already does.
- **A `sleep` in disguise was masking one of them**: `httpd.shutdown()` polls on a 0.5 s
  interval, burning half a second before the assertion while ordering nothing.
- **Two tiers, repeatedly.** The nix sandbox caught defects the dev-host tier structurally
  cannot see, three times: a `git ls-files` call with `check=True` (no `.git` in the sandbox), a
  hardcoded `== 2` that was really a claim about one machine, and a shebang that resolves only
  on the dev host. Always run both and name the tier.
- **`nix build` writes an empty marker out-path** and a `| tail` swallows the build status —
  read the derivation log, never the exit code. This bit me directly (`BUILD_RC=0` beside
  `RESULT: FAIL`).
- **A defect in `main` can masquerade as your PR's.** #849 and #872 were each blocked by
  failures they did not cause (an xdist ID collision; a real-process race). The control that
  settles it is running the same target on a clean checkout of `main` — cheap, and it twice
  stopped an agent hunting a phantom in the wrong subsystem.

- **Why the name happened at all, and why the direction changed — recorded here rather than
  under a REPLACE heading so a later status update cannot drop it.** Zach presented the system
  on 2026-08-26 and **it went well**; the three outcomes were: keep iterating locally, move
  toward decoupling it into something deployable that a **team instance** teammates read and
  write into, and give it a name simple enough to refer to. **Cairn** was chosen over Almanac /
  Muster / Atlas — one syllable, no collision with the crowded `claw` family (clawgate,
  kubeclaw, openclaw, fuzzyclaw, clawdbot), and semantically exact: a cairn is a marker left on
  a trail so whoever comes next knows the way, which is what a subsystem-index entry is. The
  team-instance half is the load-bearing part — it turns `sensitivity: client-confidential` from
  a transit question into an **authorization** question, which nothing in the system has today.
- 🔴 **The task→session→window→transcript chain RESOLVES today, but takes four tools and a jq
  join — no single command does it.** Demonstrated end to end on #360:
  `clawgate task get 360 | jq .sessions` → session `98843002…` (clawgate embeds sessions on task
  reads, with a `role` of `created`/`read`/`worked`) → `session-manager --json`, key
  `claude_session_id` → `pane_id` `%334` → tmux `scratch15:@334` on workbench → `ListAgents`
  peer name `homelab-talos-02` → transcript
  `~/.claude/projects/-home-zach-workspace-homelab-talos/98843002….jsonl`. 🔴 **`ListAgents`
  labels peers by a short ref that is NOT the session uuid** (`5e2e92`, not `988430`), so the
  window↔uuid join must go through `session-manager`, not through the peer name. The dispatch-pod
  case is the same lookup with a different backend (`devpod-<agent-name>` instead of a pane).
- 🔴 **clawgate ALREADY HAS supersede, and it was unreachable on the only real supersede.**
  `POST /tasks/merge` implements it properly (nothing deleted, loser → `complete` + comment,
  winner gains the tag union). Measured 2026-08-26: session route → **409** (refuses when either
  task is `in_progress` or `complete`, and the successor was in_progress *because* it
  superseded), `/api/tasks/merge` → **405** (no machine counterpart), `clawgatectl` → no verb.
  **The hand-rolled workaround produced a WORSE record**: a `claude-code` comment instead of the
  `user`-authored audit comment merge writes, which `taskCommentAuthor` structurally cannot mint.
  Filed as **#366**. Do not file "build supersede" — it exists.
- 🔴 **No clawgate status means "superseded".** Statuses are exactly
  `open`/`in_progress`/`ready_for_review`/`complete`. #359 is `complete`, which is false — none
  of its criteria were validated — and the truth survives only because a human wrote a comment.
  That is the part most likely to break a team instance, where nobody reads every comment.
  Dismissal is not the alternative: it **deletes** the task.
- **`/handoff`'s clawgate resolver lands in the no-worked case for a session that FILES cards.**
  `created` is terminal upstream and outranks `worked`, so a session that created a task *and*
  commented on it *and* flipped its status still reports `created`. Both handoffs this session
  hit exit 6 and recorded no field — correct, but worth knowing before you go looking for a bug.
- **A decision task is not an implementation task.** #366 was deliberately scoped to produce a
  written decision with named checkers, not code — because writing acceptance criteria before
  the design is settled is what produced #359's criteria being rewritten mid-flight.

- **The name is Cairn** (2026-08-26) — carried forward from `State now`, where a REPLACE would have
  dropped it. It names the whole three-noun layer — **sessions · claimable tasks · subsystems** —
  not just the index. Chosen over Almanac / Muster / Atlas: one syllable, no collision with the
  crowded `claw` family, and a cairn is a marker left on a trail so whoever comes next knows the
  way, which is what a subsystem-index entry is. A naming decision is durable; it does not belong
  under a status heading.
- 🔴 **#438 took THIRTEEN audit rounds and #459 took FIVE, and the SHAPE is the transferable
  part.** Early rounds found behaviour defects; later rounds found almost none and instead found
  **prose claiming coverage it did not provide** — the same defect class each tool exists to
  catch, reproduced in the artifact describing the tool. The late rounds were not waste; they are
  where the durable record got made honest. **Every round found something my own review had
  passed.**
- 🔴 **A NARROW MEASUREMENT SUPPORTING A WIDE CONCLUSION is the single recurring root cause.**
  Every 🔴 across both ladders was an instance:
  - **The one that decided a whole section wrongly:** I argued clawgate's missing `/api` merge
    route protects `user` audit authorship. `requireSession` returns `next` unchanged
    (`internal/api/auth.go:40-42`) — a no-op. My own probe disproved it and I did not notice: I
    ran a POST *from a shell*, which is a machine, and read only the status code. **The clawgate
    skill states the no-op plainly in the section I had open.** Having the fact is not using it.
  - **A degenerate combinator hid three unreachable arms.** `max(horizon, evidence_from)` reads as
    a choice, but `evidence_from` is the `min` over per-pipeline oldest runs and the horizon is a
    `max` over a SUBSET of those values, so `horizon >= evidence_from` is a *theorem*. Replaced
    with an explicit dominance rule and the invariant pinned by a test.
  - **A "runnable procedure" that silently destroys data.** It used `tags` (REPLACE) not `addTags`
    (MERGE). On #359 — `complete`, tags `devrc/rules/tooling` — there is no error path at all: the
    `touched` guard lives inside `if cur.Status == StatusInProgress` (`internal/api/notes.go:759`),
    so the call **silently succeeds** and all three tags are gone.
- 🔴 **"VERIFIED IN ISOLATION" STRUCK AGAIN, and it is the most valuable single finding.**
  `--branch` could go **entirely inert with the whole suite green**: each function was pinned, and
  nothing pinned that `main` handed the override to *both* consumers. The harness was complicit —
  the stub was a kwargs-**swallowing** lambda. **A stub that discards what it was given cannot
  witness what was passed.**
- 🔴 **A COMMIT MESSAGE DESCRIBED A FIX THAT NEVER LANDED.** The sentence it claimed to correct was
  byte-identical across both commits. Worse than the original error: a message is the durable
  record and asserts a tree state a reader can only disprove by diffing. **Check the blob, not your
  intent** (`git show <sha>:<path>`).
- 🔴 **A CORRECTION CAN UNDERSTATE THE ERROR IT CORRECTS, and that is its own failure.** I wrote
  that the tag-destroying command "would have 409'd" — it would not; it succeeds silently. Twice
  more in the same family: a paragraph that **retracted itself four lines later** (present-tense
  "still carries" beside its own "has been patched"), and the flat chip claim **re-committed inside
  the fix for a different error**, far above the section that retracts it.
- 🔴 **VOUCHING FOR A CORRECTION WITHOUT RE-READING IT is the failure one layer out.** A record said
  "a correction is posted as a comment on #391"; that comment still carried the claim the same
  commit was retracting. Name the artifact and its timestamp, then go read it.
- 🔴 **AGENT REPORTS WERE WRONG TWICE, both in ways that would have propagated unchecked.** One
  concluded a test carried no unique mutation-kill; my own excision sweep found an isolated mutant
  only it kills. One flagged a citation as off-by-one; the function really does close where I said,
  and "fixing" it would have *introduced* an error. **Re-run an auditor's measurements before
  acting on them** — including when they are correcting you.
- 🔴 **A MUTANT CAN BE AN INSERTION RATHER THAN A PERTURBATION.** I overturned a *correct* audit
  using a "mutant" on a path that held no expression to mutate — the guard short-circuits before
  it — so my ternary *added* a branch, killed by crashing rather than by any assertion, and no
  operator generates it. 803 operator-generated mutants: zero unique to the test I was defending.
  **Ask whether your mutant perturbs code that exists.**
- 🔴 **A POSITIVE CONTROL CAN BE INERT.** Adding defaults to keyword-only params every caller
  passes mutates nothing, so its "SURVIVED" was a fact about my control, not the harness. Validate
  in both directions: a real behavioural mutant must go RED and a semantic no-op must stay green.
- **Suite-size literals and absolute totals ROT in prose.** "survives all 86 tests" went stale
  inside one commit; a `repo-full-name` total moved 687 → 850 → 857 → 886 → 892 in three days.
  Say "the whole suite" and keep the RATIO; delete the figure rather than renumbering it.
- **`clawgatectl task create` returns only `{"id":N}` — never infer the id.** I cited "#367" as a
  follow-up before creating it; #367 exists and is **someone else's open card**. The real ids were
  #391 and #394. A fabricated cross-reference reads as a commitment a reader can look up.
- **The CI non-fire tool's detection power is bounded and was quantified rather than assumed:**
  5 of 7 repos in `tekton-ci` have no at-cap pipeline, so their bound is visibility-only, and 3 are
  effectively silent (bounds hours to ~1 day old). That is the number that decides whether it keeps
  earning its keep.

- 🔴 **THE STATUS LINE ROTTED THREE TIMES IN ONE DAY, AND EACH CORRECTION WAS OVERTAKEN WITHIN
  HOURS.** (1) rank 3 "STILL BLOCKED" — already done by someone else's open PR; (2) "#391 and #394
  open" — #391 had shipped an hour earlier; (3) "#394 still `open` and genuinely unstarted" —
  completed two hours later, and *that sentence was written in the commit fixing (2)*. None was
  careless; each was measured and true when written. **A ranked list that DUPLICATES a status the
  board owns will rot every time an item moves.** The board is the authority; this list is a plan.
- 🔴 **FIVE INSTRUMENTS LIED THIS SESSION, AND EVERY ONE WAS CAUGHT BY A CONTROL, NOT BY SUSPICION.**
  - `docker manifest inspect` reported the just-pushed 0.8.9 ABSENT. Run against the known-live
    0.8.8 it said ABSENT too — it fails TLS. **The reassuring-zero, and it had also silently voided
    my earlier "0.8.9 is free" check.**
  - `strings` found 0 `superseded-by` in the image — the binary is `/clawgate`, not `/app/clawgate`.
    A path error reads exactly like absence.
  - My mutation harness scored three genuine REDs as compile errors: its build check grepped for
    `cannot`, which the cap test's own failure message contains.
  - A mutant reported SURVIVED had matched a `return out` in the **wrong function**. A SURVIVED
    verdict asserts the mutant RAN where you think it did — I checked the test executed at all
    before believing it.
  - zsh ate `:c` in `$ref:containers/...` as a history modifier and returned **three identical
    hashes that were the hash of empty input**. It read as a clean confirmation. `${ref}` + a
    must-differ control caught it.
- 🔴 **A GATE'S GREEN IS A CLAIM ABOUT WHAT IT SCANNED.** I quoted `test_doc_path_rot` four times as
  evidence about `claudedocs/handoff-cairn.md`. Its `CORPUS_DIRS = ("claude", "CLAUDE.md")` — it
  never walked the file, and its own comment says so. `test_no_captured_text` (JSON only) and
  `test_no_captured_markup` (html/txt) do not cover it either. The ones that DO are
  `test_no_public_ips` and `test_no_client_hostnames`.
- 🔴 **A GUARD WRITTEN TO FIX A TOO-NARROW GUARD WAS ITSELF TOO NARROW — THREE VERSIONS.** v1 ran
  against `fakeNotes`, so hardening the real store left it green. v2 scanned one file's literal
  body, so a validating helper and a moved function both passed. v3 walks the package call graph
  transitively and **FATALs when it cannot find `AddTags` at all** — a not-found decl silently
  passing is exactly how v2 leaked. Its positive control had to be rebuilt too: the old one only
  exercised depth 1, which is what the broken version already did.
- 🔴 **THE #394 LEDGER GUARD CAUGHT ITS OWN AUTHOR ON ITS FIRST RUN.** I wrote two operator entries
  from recollection — `handleOperatorComment`, `flushOperatorReplies` — and **neither exists**
  (`dispatchOperatorTool`, `handleOpTaskComment`). Recorded in the guard's own comment: it is the
  clearest available argument that a ledger is a measurement, not a list.
- 🔴 **CARRIED FORWARD FROM RANK 6, WHICH IS NOW A REPLACE HEADING: #391's CARD GOT ASSUMPTION 2
  BACKWARDS, AND IT DECIDED THE ERROR HANDLING.** The card assumed `MaxTags` applies to the stamp
  write, so a loser at the cap would fail it — which would have made a checked 500 a *regression*.
  It does not apply: `notes.AddTags` runs `NormalizeTags` and nothing else, and `ValidateTags` has
  exactly two callers, neither on that path. **An acceptance criterion built on a false assumption
  produces a correct-looking implementation of the wrong thing** — the criterion said "either check
  it or explain why a failure is tolerable", and both branches were reasoning about a failure mode
  that cannot occur.
- 🔴 **CARRIED FORWARD FROM RANK 3: A RANKED ITEM CAN BE COMPLETED BY A PR THAT NEVER NAMES IT.**
  Rank 3 was fully implemented in open PR #948 for ~16 h before anyone here looked. Both sessions
  claimed correctly and the slugs never met (`cairn-write-path` vs `cairn-3`) — **the overlap was in
  the DIFF, not the description**, which is the one place a slug lock structurally cannot look.
  `gh pr list --state open` found it in one command. So re-scoping means **diffing the file**, not
  reading titles. ⚠ `design-claim-by-push.md`'s "What is NOT covered" list has no bullet that fits
  this exactly — not "never claimed at all" (`:661`) and not really "Reworded duplicates" (`:665`,
  one item worded two ways; these were two different items completed by one diff). Worth filing
  there rather than papering over with the nearest label.
- 🔴 **AN AUDIT FOUND A DEFECT THAT INVERTED #391's OWN SIGNAL, AND MY REVIEW HAD PASSED IT.** The
  merge union is recomputed per request, so the new stamp became an **input to the next union**: a
  loser already stamped handed it to the WINNER, which then advertised it had been superseded.
  Reachable by a retry after a mid-sequence failure (the ordering guarantee deliberately leaves the
  loser stamped *and* open) and by reopening a superseded task. It also made recovery at the tag cap
  impossible — 400 where a re-runnable 200 was documented. My comment claimed "a retry re-adds the
  tag as a no-op": true of `AddTags`, false of the union step.
- 🔴 **`error` IS NOT `failure`.** `gitops-validate` went red with `COULD NOT RUN: scripts-tests` —
  the gate says in its own output that a leg which could not run is a broken guard, not a bad
  change. Cause: `ci-manifest.txt` did not list `test_autoremix.py`; trunk had already fixed it and
  my branch was 9 behind. Syncing turned it green. **Do not debug your diff against an `error`.**
- **A blocked required check is not a reason to reach for `--admin`.** devrc's `main` requires both
  Tekton checks with `enforce_admins: true`; `gh` refused the merge and offered `--admin`. Armed
  **auto-merge** instead — it commits the decision without bypassing the gate. Distinguish *slow*
  from *wedged* on the cluster, not from GitHub: a wedged run has `childReferences` `[notify, gate]`
  and never advances; mine showed `Tasks Completed: 1 (Failed: 0)`.
- **Merging under `containers/clawgate/**` deploys NOTHING** — immutable literal pin, no Flux image
  automation. Only a ship commit bumping *both* version literals deploys. #394 needed no ship at all
  (comments + one test file, zero Go behaviour change) — stated as a decision, not skipped silently.
- 🔴 **A SQUASH-MERGED BRANCH IS NEVER AN ANCESTOR — verify by CONTENT.** Used throughout for #474,
  #489, #972, #984, #987, and for the two branch deletions. Likewise, a `clawgate-ci` green on the
  branch covers the merged tree only if the **subtree OID is identical** — measured (`ee75b13e`
  both sides) with a control proving the comparison can distinguish trees.

## How to verify
```bash
# the #391 feature, against whatever is LIVE now (re-read the pin; it is not 0.8.9 any more)
clawgatectl health
docker pull harbor.homelab.lan/library/clawgate:$(clawgatectl health | jq -r .version) >/dev/null
cid=$(docker create harbor.homelab.lan/library/clawgate:$(clawgatectl health | jq -r .version)) \
  && docker cp "$cid":/clawgate /tmp/cg.bin && docker rm "$cid" >/dev/null
grep -ac 'superseded-by' /tmp/cg.bin   # expect >=1
grep -ac 'tasks:changed' /tmp/cg.bin   # positive control, expect >=1

# the #394 ledger guard, and the whole clawgate module
cd ~/workspace/homelab-talos/containers/clawgate
nix-shell -p tailwindcss --run "cd $PWD && tailwindcss -i ./web/css/input.css -o ./web/static/app.css --minify"
nix-shell -p go --run 'go test ./... -count=1'

# host + source parity (this is what surfaces rank 2)
bash ~/workspace/devrc/scripts/drift-check.sh
```
🔴 **`docker manifest inspect` CANNOT answer "is this tag in harbor" — it bypasses the daemon's
cert config and fails TLS, reporting ABSENT for every tag including live ones.** Use `docker pull`.
🔴 **Build `app.css` from INSIDE `containers/clawgate/`** or Tailwind silently emits ~5 KB with no
utility classes and `TestOpenRoutesNoAuth`/`TestStaticAssetsServed` 404 — which looks exactly like a
code regression. Expect ~40 KB.
