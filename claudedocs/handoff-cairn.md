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
- **Branch / PR:** nothing in flight. Three PRs merged this session, each verified **by content**
  on the target branch rather than by ancestry (a squash is never an ancestor):
  - homelab-infra **#438** → `bbc373ed` — `scripts/check-ci-nonfire.py` + 88 tests (rank 4)
  - devrc **#954** → `dcc14c75` — handoff update (rank 4 done, rank 3 blocked)
  - homelab-infra **#459** → `9e5cc33e` — the supersede decision record (rank 5)
- **Cards:** #366 `complete`. #391 and #394 `open` — both filed here as follow-ups, correctly
  unstarted. `cairn-4` and `cairn-5` claims released.
- **Clean:** no worktrees of mine in either repo, no stray local or remote branches, no
  background jobs. ⚠ Two things that LOOK like stragglers are not mine — the held
  `cairn-write-path` claim belongs to another session (clawgate #371), and the four
  `~/workspace/devrc-handoff-*` worktrees are other efforts.
- **Deploy/verify:** nothing needed deploying. `check-ci-nonfire.py` is a read-only diagnostic
  run by hand; it is **not** wired into `run-ci-suite.sh` as a leg and has no consumer.
- ⚠ **`homelab-talos` carries PRE-EXISTING dirty state on `trunk`** that is not mine and that I
  did not touch: modified `.claude/skills/deploy/SKILL.md` and `flake.nix`, plus an untracked
  `claudedocs/handoff-minio-comic-flex.md` — an unsaved handoff doc, one routine `checkout` from
  silent loss.

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
1. ~~**Fix the sanitizer leak**~~ — **DONE**, see the diagnosis above. The proposed fix was
   measured not to work and was replaced: declaration-driven **withholding** of harvested
   prose, plus name substitution confined to declared identifier cells.
2. ~~**Build `cairn who <task>`**~~ — **DONE**, PR #917 (squash `c39abe31`), shipped to both
   hosts. Three things the chain description below did NOT carry, each measured and each
   changing the design:
   - 🔴 **A tmux window is TRANSIENT; a transcript is DURABLE, and collapsing them is the trap.**
     The worked example below *no longer resolves* — #360's window is gone while its 6 MB
     transcript sits where it was written. A window-keyed resolver answers "nobody" for almost
     every task older than current uptime. So each session yields TWO independent findings.
   - **The join key is not always a uuid** — 39 of 41 live windows carried uuids, 2 carried
     `ses_…` tokens. A shape-validating join silently matches nothing and reports a clean
     "no live window".
   - **`session-manager --lean` omits `pane_id`/`window_id`/`codename`** — three of the four
     things the command prints. Pinned by a test, since `--lean` is the obvious "optimisation".

   Five states are kept distinct because they all print near-nothing: `resolved` ·
   `no-sessions-recorded` (a UI-filed task genuinely has none — exit 0) ·
   `sessions-recorded-but-none-located` · `task-not-found` (7) · `bad-task-id` (2) ·
   `clawgate-unreachable` (8). The pair that matters is *"the answer is no"* vs *"there was no
   answer"*. 🔴 **An unmeasured live half is never rendered as "no window"** — if any host goes
   unmeasured the absence is UNMEASURED, and transcripts are still reported.
3. ~~**Convert the remaining positional audit reads**~~ — **NOT MINE TO WRITE: already done, in
   full, inside someone else's open PR.** Re-scoped 2026-08-28 and the correct answer was to
   write nothing. `scripts/tests/test_subsystem_store_api.py`, repo `devrc`.
   - **The named blocker merged.** `feat/cairn-p3-two-token-auth` is PR **#915**, merged
     `2026-08-27T21:15:04Z` (squash `d60c968c`). Verified by CONTENT, not ancestry: that file on
     `origin/main` and on the branch tip are the **same blob** `26688d49`.
   - 🔴 **THE ESTIMATE IN THIS LINE WAS UNDERSTATED 5.4×–7.3×, AND THAT IS THE REUSABLE PART.** "Six
     remaining … all 11 sites" was measured before #915. On `origin/main` the file carries **59**
     `audit[...]` subscript lines across **40** offending test functions (independently
     re-measured; the guard docstring's "forty" is exactly right), carrying **80** raw read lines
     by AST. So the ratio is 5.4× on sites→lines, 6.7× on remaining→functions, 7.3× on
     sites→reads — quote the range, not the friendliest end, because a correction stated at its
     low end is itself an understatement. ⚠ An earlier draft said 7.5×, which used **82** lines
     across **41** functions — that population includes the one PERMITTED site, which is not an
     offender. The offenders carry 80. A site count in prose is a measurement with an expiry date on a
     file another branch is actively growing; re-measure, never carry it.
   - **PR #948** (`feat/cairn-p3-write-path`), measured at head `92f6650e`, +4066/−112 in that
     exact file: removes **58** positional reads and adds **2**. Three `audit[...]` occurrences
     remain in **12,262** lines — two `#` comments (one pre-existing at 3341, one added at 3648)
     and one docstring line (5781, added). **Zero live positional reads**, re-derived by running
     the guard's own AST algorithm: 0 offenders.
     🔴 **An earlier draft of this bullet said "both additions are inside comments" AND "all three
     in docstrings" — mutually exclusive, since the 2 are a subset of the 3.** It also said
     12,264, which was a fact about `str.splitlines()`, not the file: it contains one **U+2028**
     and one **U+2029**, which Python splits on and `wc -l` does not. Both wrong; the
     load-bearing conclusion (zero live reads) was right, which is exactly how a wrong number
     survives review.
   - **Of the three guards named here, #948 adds ONE** — `test_no_test_INDEXES_a_live_audit_list`.
     `test_every_audit_reading_test_goes_through_the_shared_helper` and
     `test_the_call_site_THRESHOLD_is_load_bearing_not_decorative` **already exist on `origin/main`
     and are byte-identical at `92f6650e`** — an earlier draft credited all three to #948.
     ⚠ **That is a statement about THESE THREE, not about #948.** A previous wording said "it ships
     ONE durable guard, not three", which is false at its widest reading and understates the PR in
     the same breath as saying its merged-tree gate is still owed — the disarming shape again, in
     the correction this time. Measured: #948 takes the file from **360 to 496 test functions**
     (+137, −1), and several additions are durable structural guards unrelated to audit reads
     (`test_every_ledgered_write_route_actually_dispatches`,
     `test_the_write_route_ledger_is_the_whole_write_route_set`,
     `test_the_verb_ledger_is_the_whole_bound_verb_surface`,
     `test_the_route_table_and_every_handler_signature_AGREE`, and —
     with some irony given the count error above —
     `test_the_set_this_class_exercises_is_the_set_splitlines_ACTUALLY_splits_on`).
     🔴 **And the pairing was misleading beyond the miscount:** that pre-existing anti-vacuity test
     pins `_drain_output_call_sites() == 3`, a population of `drain_output(...)` calls **unrelated
     to the ~40 `await_audit` conversions**. So nothing pins the converted call-site count, and
     the conversion is *not* protected against silent shrinkage the way "anti-vacuity" implies.
   - 🔴 **THE GUARD'S EXEMPTION IS WALKABLE, and this doc repeated its docstring's claim
     unchecked.** The waiver is `any(isinstance(n, ast.Attribute) and n.attr == "wait_closed" for n
     in ast.walk(fn))` — a token anywhere in the function body, **reachability-blind**. Adding
     `if False: _unused.wait_closed()` after a raw read exempts the test, on an undefined name in
     dead code. So "permits exactly one site structurally, it cannot rot into a free pass the way
     a name exclusion would" is **false**; only "never by name" survives. The guard is real and it
     caught the mutation below — it is just weaker than it says.
   - **Mutation-tested rather than trusted** (detached worktree at `92f6650e`, pristine restored):
     reinserting one `assert audit[0]` into a converted test drove
     `test_no_test_INDEXES_a_live_audit_list` **red with its own message**, naming the offending
     function and line. Killed by the guard's own assertion — not by a crash, not by a neighbour.
     Independently reproduced by an auditor at a different call site.
   - 🔴 **THE HARD LOCK COULD NOT SEE THIS AND THE PR SWEEP COULD.** #948 had been public ~16 h.
     The other session claimed `cairn-write-path` (clawgate #371, criteria 4–7, the *write path*);
     this reader derived `cairn-3` for the test conversion. **Both sides claimed correctly and the
     slugs simply never collided** — the overlap was in the DIFF, not the description.
     ⚠ **`design-claim-by-push.md`'s "What is NOT covered" list has no bullet that fits this
     exactly, and pretending otherwise has now been wrong twice.** A first draft cited "a
     collision on work that was never claimed at all" (`:661`) — plainly wrong, both sides
     claimed. The correction cited "Reworded duplicates" (`:665` → `:542`, *"a semantically
     identical item that two sessions word differently"*), which is a loose fit at best: these
     were two genuinely DIFFERENT items — a clawgate task and a handoff rank — that happened to
     be completed by one diff. The honest statement is that the list is **missing this case**:
     *a correctly-claimed item whose PR incidentally completes a separately-claimed one.* Worth
     adding there. Either way `gh pr list --state open` is what caught it, and the sweep is not
     a fallback.
   - **Left to do: the merged-tree gate, and no evidence anyone has run it.** #948 was `MERGEABLE`/`CLEAN` with
     both checks green when measured at `92f6650e`; at
     **2026-08-28T17:52Z** its head is `47c9849b`, state `BLOCKED`, both checks `PENDING`.
     🔴 **An earlier draft said a `devrc-integ-948` worktree on `integ/948-merged` "already
     exists" and told the reader not to duplicate that work. It does not exist** — no such
     worktree on either host, no such local or remote branch. It was observed earlier in one
     session and was gone by the end of it. Under `strict: false` a green check is a claim about
     the BRANCH, so the merged-tree gate is genuinely still owed and the sentence that disarmed it
     was built on a stale observation of my own — the failure this file's own rule names.
4. ~~**Instrument the Tekton non-fire rather than chase it**~~ — **DONE**, homelab-infra #438
   (squash `bbc373ed`), `scripts/check-ci-nonfire.py` + 88 tests. 🔴 **THE CARD'S OWN SPEC WAS
   THE FIRST THING THAT HAD TO GO.** "Flag required checks pending with no PipelineRun for that
   sha" is v1, and v1 was wrong twice over: a pending status is posted from INSIDE the run
   (`devrc-ci-pipeline.yaml:244`), so its presence PROVES a run existed; and the runs were absent
   because the pruner keeps 100 **per pipeline**, so devrc's own horizon was ~37 h while the PR
   it "caught" was 44 h old. Worse, **v1 could not see its own motivating case** — a genuinely
   dropped delivery posts NO status, leaving an EMPTY rollup, and v1 matched on PENDING entries.
   The retraction is a public comment on #438, not an edited body.
5. ~~**#366 — the supersede decision card**~~ — **DONE**, merged `9e5cc33e`
   (homelab-infra #459), card `complete`. Record:
   `homelab-talos/containers/clawgate/supersede-decision-2026-08-28.md`.
   🔴 **YOUR CARD'S CENTRAL ASSUMPTION WAS FALSE, AND THAT IS THE RESULT.** It asked what
   happens *"if the `user`-authored audit comment turns out not to be a genuine integrity
   property"*. It is not one on the LAN: `requireSession` returns `next` unchanged
   (`internal/api/auth.go:40-42`), so `POST /tasks/merge` answers unauthenticated LAN POSTs —
   measured, no token/cookie/header, 409, which means the handler RAN. Any machine can already
   merge and already mint `user`-authored comments (`notes.go:1567`). The "machine gap" is a
   routing detail, not a boundary.
   Decisions: **(a) both 409 arms STAY unchanged** — reversed mid-work, because the obvious
   narrowing is *unsafe*: `project:` is not a routing tag (`routingNamespaces` =
   {runbook, initiative, gate, auto}), so a `RoutingTags` predicate cannot see a projectless
   winner adopting the loser's project. **(b) no machine route**, but labelled what it is — a
   default to the status quo, not a conclusion the evidence forces. **(c) no new status**;
   descriptive `superseded-by:<winner>` tag, with the *visual* half qualified (3-chip cap,
   routing-first ordering, Done lane collapsed).
6. **#391** — make a UI supersede as legible as a hand-rolled one: `merge.go` effect (3) also
   adds `superseded-by:<winnerID>` to the loser. Additive only; nothing refused today becomes
   permitted; **no backfill**. Repo: `homelab-talos`, `containers/clawgate/`. Its body was
   patched mid-session to retract the auth reasoning it inherited — read the card, not the
   first draft of the record.
7. **#394** — three source comments claim a machine cannot author as `user`, without naming the
   path that holds on; one of them decided (b) in my first draft. Repo: `homelab-talos`.
   🔴 **NOT "authenticate the LAN NodePort"** — trusted-open is deliberate and documented
   (`api/auth.go:35-39`), and a card to fix something that is off on purpose is worse than no
   card. Scope is the comments' claims, not the model.
8. **No card filed for the §(a) guard narrowing, deliberately.** It makes a merge *possible*
   where one is refused today, on a route mutating two tasks with no transaction, and any
   narrowing must test the project delta explicitly AND answer for the second winner-side write
   (a comment a mid-flight agent reads via `GET /agent/task`) that no tag predicate gates. §5.3
   of the record is the reasoning a card would cite.

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
  all 11 sites" was 5.4×–7.3× low within a day because a concurrent branch was growing the same file.
  Same family as the suite-size literals below, but worse: a suite total reads as trivia, whereas
  a site count reads as *scope* and is what a session budgets against. Re-measure at the moment
  you act; never carry the number forward.
- 🔴 **A COUNT IS A CLAIM ABOUT THE TOOL THAT PRODUCED IT.** The rank-3 rewrite said
  `scripts/tests/test_subsystem_store_api.py` was 12,264 lines; `wc -l` says **12,262**. Both are honest: it holds one **U+2028** and one
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

## How to verify
```bash
# rank 4 — the CI non-fire checker, read-only (gets/lists only)
KUBECONFIG=$KC_HOMELAB python3 ~/workspace/homelab-talos/scripts/check-ci-nonfire.py \
  --repo innovation-upstream/devrc
PYTHONDONTWRITEBYTECODE=1 python3 ~/workspace/homelab-talos/scripts/tests/test_check_ci_nonfire.py

# rank 5 — the decision record exists on trunk (content, never ancestry)
git -C ~/workspace/homelab-talos show \
  origin/trunk:containers/clawgate/supersede-decision-2026-08-28.md | head -1

# the measurement that decided (b) — expect a HANDLER response (409), not 401
curl -s -X POST http://192.168.50.250:30302/tasks/merge -d 'winner=360&loser=359'
```
🔴 **`run-ci-suite.sh` reports 6 red files and they are red at the base commit too** — five are a
missing local `pyyaml` (all pass under `nix-shell -p python3Packages.pyyaml`), one is
`test-check-subsystem-store-phase1.sh` at `pass=7 fail=17` on both sides. Do not attribute them to
this work; run the base-commit control before believing otherwise.
