# Handoff: handoff-doc-prune-exit — 2026-09-29

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Give `/handoff`'s append-only sections an exit. `handoff_doc.py` could add to
`Open investigations`/`Findings`/`Gotchas` and never remove, so a doc over its ceiling had no
sanctioned remedy and the only escape was `--override-size-ratchet`, which SHIPS the
over-ceiling doc.

🔴 **A NEW ARC, and `--new-effort` is deliberate.** `claudedocs/handoff-handoff-resume-prune.md`
is the neighbour and it is **CLOSED** — verdict ADDRESSED, condition met on all three clauses at
`b1bee35b`; it ported prune concepts into the two skills and did not give the doc corpus a
delete path. This arc builds that path and fixes one false claim found on the way.
- **closing-condition:** `check` — **devrc#1919 is merged to `origin/main` AND `/handoff` step 5
  names `--prune` as the first remedy for `size-ratchet`**, verified by
  `grep -c 'prune' <(git show origin/main:claude/skills/handoff/SKILL.md)` returning non-zero
  **on the `size-ratchet` line itself**, so an executor that hits the ceiling is routed to the
  exit instead of the override. 🔴 The second clause is BLOCKED on an operator decision (see
  ranked item 2) and the arc cannot close without it.

## State now
- 🔴 **THE ARC IS CLOSED. VERDICT: ADDRESSED.** `devrc#1943` merged as **`022da5e0`** (squash,
  2026-09-30T22:49:24Z). Clause 1 was already met; clause 2 now returns **1** where it returned
  0 all session. Verified by CONTENT on `origin/main`, never by ancestry — a squash makes
  `merge-base --is-ancestor` false forever.
- 🔴 **AND VERIFIED AT THE CONSUMER, WHICH THE CLOSING CONDITION CANNOT SEE.** `~/.claude/skills`
  is a `home.file` **store copy** (`nix/home.nix:1427`), so merging changed nothing an executor
  reads. `bash scripts/ship.sh --no-remote` converged this host; the before/after pair is the
  evidence, not the exit code:

  | | pre-switch | post-switch |
  |---|---|---|
  | store path | `…qvxnx808jrby…` | **`…w3y3bhhv6g4w…`** |
  | bytes | 20,031 | **20,222** |
  | sha256 | `a12ceaf80c9570a7` | **`5d3fa0266a1b78f2`** |
  | field names `--prune` | **0** | **1** |

- ⚠ **THE LAPTOP IS DELIBERATELY UN-SWITCHED** — `--no-remote` was chosen because ship.sh's
  remote leg fails on this box (exit 255, *"NO candidate address answered"*) and prints a
  per-host `✅ VERIFIED … + switched` BEFORE the line saying agreement was never compared. The
  run's own summary says it: *"1 host (local=workbench); cross-host agreement NOT COMPARED."*
  That host still serves the override-only field. See ranked item 2.
- ⚠ **CARRIED FORWARD — THE FROZEN `Goal` ABOVE STILL EMBEDS THE RETRACTED CHECK, AND IT IS
  WRONG TWICE OVER.** Its closing-condition line spells `grep -c 'prune'` against the
  `size-ratchet` LINE. That is (a) line-scoped, so it returns 1 regardless — step 5 is one
  physical line whose trailing pointer names `--prune` — and (b) prefix-blind, so it returns 1
  even on a field naming only `--prune-count`, which the tool refuses. The condition is frozen at
  round 1 and is left as written; **`How to verify` step 1 is the authoritative form.** The arc
  graded ADDRESSED against that corrected check, not against the frozen text.
- ⚠ **CARRIED FORWARD — the `Goal` names `#1919` while `State now` credited `#1926`; both are
  merged** (`#1919` `643fd0ef` 2026-09-29T19:51Z, `#1926` `ed947e1a` 2026-09-30T17:17Z), so
  clause 1 holds under either reading and the ambiguity never had an effect. Recorded so a future
  reader of the frozen Goal does not go looking for an unmerged #1919.
- 🔴 **NO `MAX_BYTES` RAISE HAPPENED, AND THE FORK THIS DOC BLOCKED ON WAS NEVER REAL.** The
  previous ranked item 1 was carried as an operator decision — raise the constant or amend the
  closing condition — on **87 B** of headroom measured at `dc159b07`. `f1801784` had already
  shrunk `SKILL.md` to 20,031 B, leaving **269 B** against **150 B** of wiring. `MAX_BYTES`
  (21,200) and `MIN_HEADROOM_BYTES` (900) are untouched on `origin/main`. Final size **20,222 B**,
  **78 B** of slack to the enforced 20,300 — and that is tight: the owning module sizes a real
  edit at ~486 B, so the next content edit to `SKILL.md` reds the headroom test and must evict
  first.
- **The ladder ran FOUR rounds (0–3) and was STOPPED BY THE ATTRIBUTION GATE, not by a clean
  round.** `audit-dispatch.py --round 4` exits **5**: rounds 2 and 3 both changed zero payload
  lines, so the ladder was auditing scaffolding it had written itself. Ending there is the
  gate's stated correct outcome.
- 🔴 **NINE FINDINGS ACROSS THE ARC, EVERY ONE A FALSE CLAIM IN MY OWN PROSE, ZERO BEHAVIOUR
  DEFECTS.** Rounds 0–3 added: a wrong provenance sha in three places, a vacuous assertion, a
  fresh byte figure written while deleting a stale one, a docstring crediting the wrong
  assertion (four consecutive times), a commit message claiming a fix landed in the PR body when
  it had not, and an evidence record whose two cited mutants included one that was already red.
- **Both CI tiers green at the merged head.** Four `tekton/devrc-*` statuses `success` at
  `22:48:27–29Z` against a `22:29:17Z` commit (so not a stale rollup), and round 3 independently
  ran the merge-gating sandbox tier: `SCOPE: FULL (30 of 30 hermetic targets)`, `RESULT: PASS`.
- **Six suites: 888 passed** (the 885 baseline + three guards). The six are named in
  `How to verify`; a different plausible six gives 1,021.
- **Claim `handoff-doc-prune-exit-1` RELEASED. Worktree removed** (exact path). The branch was
  auto-deleted on merge. Base clone is on `main` at `022da5e0`; its two untracked
  `claudedocs/scope-chief-*` files predate this session and are not mine.
- 🔴 **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5** again. An unknown
  session id answers 200 with an empty array, so that zero cannot distinguish "touched no task"
  from "wrong id". **Not a clean bill of health.**

## Open investigations — live diagnosis state

### devrc's PR pipeline and its main pipeline are different gates, and that made a shared red hard to attribute
- as-of: 2026-09-29
- **Symptom + exact repro:** a PR's pytests status and `main`'s post exist under **different
  context names**, so "main is green" and "the PR's gate is green" are not the same claim and a
  base-branch red is invisible from `main`'s status. Reproduce:
  `gh api repos/innovation-upstream/devrc/commits/main --jq .sha` then
  `gh api "repos/innovation-upstream/devrc/commits/<sha>/status" --jq '[.statuses[].context]'`
  and compare against the same call on any open PR's `headRefOid`.
- **Observed (with values):** `main` posts `tekton/devrc-main-{pytests,gotests,nodetests,cairn-client-runs}`;
  a PR posts `tekton/devrc-{pytests,gotests,nodetests,cairn-client-runs}` — no `-main-`.
  Collected counts differ per run: **24,523** (PR #1919 at 15:34Z), **24,680** (PR #1920),
  **24,694** (PR #1919 after refresh), **24,251** (#1885), **24,076** (#1874). `main` at
  `8bfbd0ab` read `state: success` on all four while two open PRs were failing one shared test.
- **Ruled out:** "the PR red was caused by the PR" — `via: measurement`; the failing test was
  `test_the_real_browser_skill_is_under_the_target`, PR **1922** failed the **same** one, other
  failing PRs failed **different** ones (#1913 a shebang check, #1890 absolute paths, #1872 a
  store ledger), and `scripts/browser-bridge/SKILL.md` was **byte-identical** between my merged
  tree and `main` (12,020 B, sha `c60eeb261ac2`).
- **Ruled out:** "the base branch is chronically broken" — `via: measurement`; the skill was
  **12,981 B** at `79a9b22a` and earlier (over the 12,038 enforced budget) and **12,020 B** from
  `f291e16a` (#1917) onward. That fix landed **16:12Z**; my CI ran **15:34–15:53Z**. Refreshing
  the branch onto current `main` turned the gate green — so it was a ~1h window, not chronic.
- **Ruled out:** "devrc's gate is permanently red on `main`" — `via: measurement`; **THIS WAS MY
  OWN CLAIM AND IT IS RETRACTED.** I reproduced 4 failures in `test_run_tests_targets.py` using
  another repo's venv **outside the devrc devShell**. Inside the devShell the same file is
  **green** (148 passed with `test_skill_audit.py`). The failures were an artifact of my runner.
- **Leading hypothesis:** the two pipelines run different selections (the collected counts never
  match), so `main`'s green is a weaker statement than a PR's green and cannot be used to
  attribute a PR red. `via: assumed` — I did not read either pipeline definition.
- **Next probe:** read the Tekton pipeline definitions and answer whether the `-main-` pipeline
  runs a narrower target set. If it does, say so in the repo's CI docs, because the natural
  reading of "main is green" is currently wrong.

## Next steps (ranked)
1. **Decide `jev-ui-demo`'s doc budget, then close `ZacxDev/jev-ui-demo#14`.** Untouched by this
   session and the only substantive item left. Round 0 of that PR found
   `handoff_budget.GRANDFATHERED` holds **71 foreign entries** at allowances up to **425,984 B**,
   and that doc is bound by the bare 65,536 **only because `jev-ui-demo` is not in
   `handoff_index.REPO_ENV_HANDLES`** — so it was never in the population that block was measured
   over. One ledger line would have been the precedented treatment and would have made #14
   unnecessary. `IN FLIGHT: ZacxDev/jev-ui-demo#14`. Files: `scripts/lib/handoff_budget.py`,
   `scripts/lib/handoff_index.py`.
    forcing: none
2. **Converge the laptop, or accept that it serves the old skill.** Closing condition, runnable
   on that host: `tr '·' '\n' < ~/.claude/skills/handoff/SKILL.md | grep -F '`size-ratchet`' |
   grep -cE -- '--prune($|[^-[:alnum:]])'` returns **1**. 🔴 A plain `ship.sh` run will NOT do it
   — its remote leg exits 255 here with *"NO candidate address answered"* while the same probe
   command succeeds standalone (cause unmeasured; see the `scripts` entry in the cairn store).
   Either pin `REMOTE_SSH` or run `ship.sh --no-remote` ON that host. Files: `scripts/ship.sh`.
    forcing: none

## Defects (batched)
- **`scripts/tests/test_handoff_doc.py:11769` says "SKILL.md is at ~25 B of headroom"** in the
  present tense against a real 978 B under the hard ceiling / 78 B under the enforced budget.
  Pre-existing, out of every audited range, untouched by #1943 — recorded here rather than filed,
  because it is a one-line prose fix with no owner and minting an object for it would be the
  object-leak this repo's rule 11 forbids. It is the same claim-with-a-shelf-life class the whole
  arc is about, surviving in the same file.
- **`field.index("--override-size-ratchet")` takes the FIRST occurrence**, so a correctly-routed
  field that names the override early in a warning reds. Pre-existing semantics, accepted and
  noted at the site; no positional guard can distinguish it from a genuine misroute.
- **`scripts/browser-bridge/SKILL.md` has 18 B of headroom** (12,020 / 12,038) and blew its
  budget once on 2026-09-29, reddening two unrelated PRs for ~1 h.
- **No pre-push hook is installed in this clone** — `core.hooksPath` unset, only
  `prepare-commit-msg`.
- **`scripts/gate.sh` cannot print PASS off a scoped run** (exit 91).

## Gotchas / decisions / dead-ends
- 🔴 **devrc's tests REQUIRE its devShell, and the wrong python fails in two different lying
  ways.** With another repo's venv, a full-suite run dies at **collection** with
  `INTERNALERROR ... No module named 'psycopg2'` — "3 errors in 13.24s", **zero tests executed** —
  and a targeted run of `test_run_tests_targets.py` reports **4 failures that do not exist**. Both
  cost me a wrong conclusion. Correct form:
  `nix develop /home/zach/workspace/devrc --command bash -c "cd <worktree> && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest <files> -q"`.
  `.envrc` here is gitignored and says only `use opencode`, so a worktree has no shell; the
  `nix develop` form needs no direnv.
- 🔴 **A PUSH FROM A DETACHED WORKTREE (`git push origin HEAD:<branch>`) LEAVES THE LOCAL BRANCH
  REF BEHIND, AND MERGING `main` INTO THAT STALE REF SILENTLY DROPS YOUR OWN WORK.** The remote
  had `47d6bd52`; the local branch had never contained it. I made a worktree on the local branch,
  merged `origin/main`, and produced a commit with `main` and **none of my change** — and the tell
  was a `grep -c` returning **0**, which reads as "the merge is fine" rather than "the change is
  absent". The non-fast-forward rejection is what caught it. **Check
  `git merge-base --is-ancestor <pushed-sha> <local-branch>` before merging anything into a branch
  an agent pushed for you**, and repoint with `checkout -B <branch> <pushed-sha>` — never
  `reset --hard`, never a force-push.
- 🔴 **ATTRIBUTE A SHARED CI RED BY THE FAILING TEST ACROSS OTHER OPEN PRs, NEVER BY THE JOB
  NAME.** One command settled an hour of doubt:
  `for n in $(gh pr list --repo <r> --state open --limit 8 --json number --jq '.[].number'); do ...
  select(.context=="tekton/devrc-pytests") ...; done` — two PRs failing the *same* test that
  neither touches is a base-branch condition; PRs failing *different* tests are their own.
- ⚠ **A GitHub status `description` is capped at 140 chars and `target_url` can be `null`**, so a
  multi-failure run shows you **one** failing test name and a truncated total. `gh api
  repos/<r>/statuses/<sha>` gives the history but no more text. Plan to reproduce, not to read.
- 🔴 **`write-gate.md` is an EVICTION SINK — do not prune or split it.** §C/§D/§F/§G were moved
  there whole, and `/prune-skill` forbids pruning a sink. Its growth is **uncapped** (100,470 B
  today); `MAX_BYTES` caps `SKILL.md` only. A byte objection aimed at this file is aimed at the
  wrong one.
- 🔴 **The `forcing:` emphasis rule is a MECHANISM BY POSITION, not a list, and stating it as a
  list is a named failure in that very section** (`write-gate.md:294`: *"a GRID, not a list … this
  section first named one admission, a delta audit raised it to four, and a second delta audit
  measured ten"*, machine-enforced for the module at `handoff_doc.py:684`). Positions: **leading**
  is a one-character negative lookbehind `(?<![A-Za-z0-9])` that consumes nothing, so **any
  number of any non-alphanumeric characters** may precede the key; **post-key** and **post-colon**
  are each `_MARKUP` = three-or-fewer from the `*_` backtick-tilde class. So
  `****forcing:** gate` **parses** while `forcing:****gate` does **not**. My first correction
  enumerated five spellings and was itself incomplete in the same direction — round 0 caught it.
- 🔴 **WRITING THE LITERAL FORCING-FIELD TOKEN IN A RANKED ITEM'S PROSE HIJACKS THAT ITEM'S OWN
  FIELD, and the refusal names a kind you never wrote.** Hit landing THIS doc: item 2 discussed
  "demoting the `<token>:` variant catalogue" and step 5 refused `status=unforced` with
  `[unknown kind: 'variant']` — `_FORCING` matched the prose occurrence and captured the next word
  as the kind, while the item's real tag sat on a later line unread. **Never write that token
  inside a ranked item; say "the forcing-field" or rephrase.** Same family as the emphasis grid:
  the pattern is deliberately permissive about what precedes the key, so prose *about* the
  mechanism is indistinguishable from a use of it.
- 🔴 **Writing a backslash-u-0000 escape through a file-writing tool MATERIALISES the byte.** It
  bit me **twice in one operation**: git refused a commit message for it, and I then shipped one
  into a handoff doc at byte 53872 and had to fix it in a follow-up commit. Write escape-bearing
  text via `python3` and sweep every staged blob. ⚠ And `checkNoControlBytes`-style guards that
  read `git ls-files` are blind to files a change ADDS — stage before sweeping.
- **Piping pytest through `tail` reports the FILTER's exit status.** It showed green over a
  6-failure run this session. Count the result lines.
- **Round 0 earned its cost on a one-sentence prose PR**, which is the case against running it:
  it refuted two of my published claims, found the correction committed the error it was fixing,
  and established that the fact lives in **three** places and was guarded in **zero** (deleting
  the 57 B emphasis clause from `SKILL.md` left **642 passed, 0 failed**). The guard, not the
  sentence, was the fix.

- 🔴 **A REFUSAL MESSAGE IS A CLAIM WITH A SHELF LIFE, AND NOTHING IN THIS REPO WAS WATCHING IT.**
  `--prune` shipped in #1916 and rule (p)'s refusal went on asserting *"this tool cannot shrink
  them for you"* — a sentence that was true when written, that the very next feature falsified,
  and that steered authors away from the exit at the exact moment it applied. **A false remedy
  note is worse than a missing one: it stops the reader looking.** The general tell is a message
  that says what the tool CANNOT do; that is the clause a new feature invalidates, and no test
  fails when it goes stale. When you ship a capability, grep the refusals for denials of it.
- 🔴 **THE ELIGIBLE LEVER WAS NOT THE ONE THE RANKED ITEM NAMED, AND THE OWNING TEST SAID SO IN
  WRITING.** Item 2 was framed as a `MAX_BYTES` raise + `SKILL.md` wiring. But
  `scripts/tests/test_handoff_skill_size.py`'s eviction playbook ranks its own remedies —
  **1. move guidance INTO the tool · 2. demote a block to `reference/` · 3. raise `MAX_BYTES`**
  — and the ledger on the constant says *"the answer to the next round is lever 1 or 2, not
  another raise"*, having last **ratcheted DOWN**. Lever 1 cost **0 bytes** in the skill body and
  fixed a live false claim as a side effect. **Read the owning test's own playbook before
  accepting a handoff's framing of a byte problem** — the previous session's ranking was a
  hypothesis about the remedy, not a measurement of it.
- 🔴 **A SHORT-CIRCUITED ASSERTION IS AN UNREACHABLE GUARD, AND THE RED BASELINE HIDES IT.** Both
  new guards went red at the base — but in that run the `--prune-count` assertion **never
  executed**, because the `--prune` assertion above it fails first. A red-at-base / green-at-HEAD
  matrix is therefore NOT evidence that every assertion in the test works. The control is an
  **isolated** mutation of the narrowest expression: dropping only the `--prune-count` token
  (needle count asserted 1) reds that assertion alone with its own message, 1 failed / 1 passed,
  and the sibling test stays green — which also proves the two guards cover distinct properties.
- ⚠ **`git push origin HEAD:<branch>` from a DETACHED worktree is rejected** — *"Neither worked,
  so we gave up. You must fully qualify the ref."* Git cannot guess whether an unqualified name
  means a new branch when the source is a bare commit object. Use
  `git push origin HEAD:refs/heads/<branch>`. Cheap, but it costs a round trip every time and the
  error text does not read like a detached-HEAD problem.
- **`--prune`'s scope is rule (q) at `scripts/lib/handoff_doc.py`, and its rationale is worth
  reading before touching either rule**: it is the deliberate MIRROR of rule (p), not a second
  half of it. Its header records the measured cost of having had no exit rule — not the bytes,
  **the hand edit**: with the sanctioned writer unable to remove a line, the remedy reached for
  was an `Edit` of the committed doc, which bypasses every gate at once, and one such edit
  shipped a ranked list that misrepresented the state of the work.
- **The `size-ratchet` refusal has a pinned ledger test that is NOT the remedy list.**
  `SIZE_RATCHET_WHO_MAY` is pinned across five sites (refusal, override note, `--help`,
  `SKILL.md`, `write-gate.md` §I) because three of them once said "OPERATOR" while the ruling was
  the opposite. Editing the remedies does not touch it — but editing who may pull the override
  touches all five, in one commit.

- 🔴 **A REFUSAL MESSAGE IS A CLAIM WITH A SHELF LIFE, AND NOTHING WATCHES IT AGE.** `--prune`
  shipped in #1916 and rule (p)'s refusal went on saying "this tool cannot shrink them for you".
  **A false remedy note is worse than a missing one: it stops the reader looking.** The general
  tell is any message stating what the tool CANNOT do — that is the clause a new capability
  invalidates, and no test fails when it goes stale. When you ship a capability, grep the refusals
  for denials of it.
- 🔴 **A RETRACTION IS A TREE-WIDE SWEEP, AND THE SITE-AT-A-TIME FIX FAILED THREE TIMES ON ONE PR**
  — module missed, then `write-gate.md` missed, then the test module itself. The cure is a ledger
  that scans a globbed set and COUNTS what it saw; the cure is NOT another careful sweep by hand.
- 🔴 **A GUARD'S ANTI-VACUITY CHECK MUST PIN THE CALL SITE, NOT THE FUNCTION.** Round 2 gutted a
  loop to `for … in ():`; a guard asserting the site-list function returns a full list passes that
  mutant unchanged. And a TOTAL floor is satisfiable by the files that cannot fail — a call site
  filtered to `.md` cleared `files>=4, seen>=3` while never reading the payload module. **Require a
  per-site contribution from the files that provably contain the thing.**
- 🔴 **A NORMALISER THAT HANDLES COMMENTS AND NOT STRING LITERALS COVERS THE COPY NOBODY READS.**
  916 lines of `handoff_doc.py` end in a bare closing quote — the refusals and `--help` are built
  from adjacent literals, and that is where the offending sentence originally lived. A phrase split
  across two literals was not merely unflagged, it was **not counted** (`seen=0`).
- 🔴 **A MARKER MATCHING INSIDE AN IDENTIFIER IS A LOAD-BEARING ACCIDENT.** One cleared occurrence
  passed only because `retracted` matched inside the constant name `RETRACTED_BY_RULE_Q` 25 chars
  away — so a pure RENAME of that constant made the sweep fail on its own ledger. Same shape as
  `ENDED` inside `appended`, surviving the narrowing that `ENDED` caused. Word-bound the markers.
- 🔴 **EVICT THE CLOSED INVESTIGATION; KEEP EVERY LIVE GOTCHA IT PRODUCED.** The jev eviction moved
  two corrections into the archive and left the refuted cure standing in `Gotchas` — satisfying the
  move rule in FORM while making the document actively misleading. "Resolved" describes the
  diagnosis, never the hazard it found.
- ⚠ **A correction APPENDS under its own heading; it does not replace.** Trying to prune the
  superseded block in the same run is refused `[ambiguous]`, because the append creates the
  duplicate heading itself — and rule (c) deliberately keeps a superseder AND what it superseded.
- ⚠ **`--prune-count` is the tool's unit, not `wc -l`** — blank lines are not "named". I passed 44,
  the guard refused, 42 were removed, and my own pointer then recorded the number the guard
  rejected. A count in prose is a claim.
- ⚠ **`handoff_doc.py --push` refuses from a DETACHED worktree** (*"detached HEAD and no --branch
  given; refusing to guess"*) and writes nothing. Attach the worktree to the branch.
- ⚠ **The commit guard blocks a plain `git commit` on `main`, but `handoff_doc.py` does not see it**
  — the tool runs git inside Python, so no PreToolUse hook fires. The two paths do not behave alike.
- ⚠ **`grep -cxF "$line"` eats a leading `-` as an option** and reports every such line as
  ambiguous. Use `grep -cxF -- "$line"`; the false "AMBIGUOUS" rows were my instrument, not findings.
- ⚠ **Carried from a now-closed defect, because the lesson outlives it:** an earlier PR body on
  this arc carried two false counts ("four guards" was three; "81 pinned phrases / 2,050 B" was not
  reproducible — a hand re-read gave 46 / 1,468 B against a blind round's 23). 🔴 **That quantity
  has been measured three times with three answers; never let any count of it become load-bearing.**

- 🔴 **FIVE AUDIT ROUNDS, FIVE FINDINGS IN MY OWN PROSE, ZERO BEHAVIOUR DEFECTS — that ratio is the
  arc's headline.** r0: a docstring claimed coverage its sibling guard did not have. r1: the
  refusal said eviction needed its own run and commit — harmful, it prescribed a pointerless
  deletion. r2: a "positive control" stayed green with the guard reading ZERO files. r3: a
  present-tense claim sat **two lines above my own note saying it had been converted**. r4: both
  behavioural fixes were UNGUARDED — mutants I had run by hand and never committed, so reverting
  either left the suite green at 637 passed. **When a round rewrites an explanation, audit the
  explanation at least as hard as the code.**
- 🔴 **AN ANTI-VACUITY GUARD MUST PIN THE CALL SITE, AND ITS POSITIVE CONTROL BELONGS IN THE
  GUARD'S OWN BODY.** My first fix asserted the site-list FUNCTION returned a full list; the
  mutation is at the CALL SITE, so the mutant passed 4/4. What works is counting what the sweep
  actually READ and asserting a floor before asserting no offenders. **Report the pair, never the
  zero alone.** And a TOTAL floor is satisfiable by the files that cannot fail — a call site
  filtered to `.md` cleared `files>=4, seen>=3` while never opening the payload module.
- 🔴 **A GUARD THAT REQUIRES THE DEFECT TO PERSIST PUNISHES THE CLEANUP IT EXISTS TO CAUSE.** The
  per-site floor first required ≥1 OCCURRENCE of the retracted phrase from two named files — so an
  author who FINISHED a retraction was failed by the guard whose whole purpose is that state.
  Assert the file was READ (bytes), never that it still contains the thing. Same shape as
  RULES' *an EMPTY RESULT cannot distinguish two mechanisms*: a per-site zero means skipped OR
  clean, and the message named only the first.
- 🔴 **A MUTANT YOU RAN BY HAND IS NOT A GUARD.** r3's commit message listed "m5 … now RED" as a
  control; that mutant was never committed as a test, so r4 measured both of r3's behavioural
  changes revertible with the suite green. **If a fix's control is not in the tree, the fix can
  revert silently.** Both are now assertions watched red against each reverted line.
- 🔴 **A NUMBER WITHOUT ITS POPULATION IS NOT REPRODUCIBLE, EVEN WHEN IT IS RIGHT.** r4 could not
  reproduce "885 passed across the six suites" and flagged it — correctly, because no commit
  message said WHICH six. Re-run, it is exactly 885. The figure was right; naming the population
  is what makes it a claim. The six are now listed in `How to verify`.
- ⚠ **`gh pr merge` printed NOTHING on success** — no confirmation line, exit 0. Verify a merge by
  `gh pr view --json state,mergedAt,mergeCommit` plus a CONTENT check on `origin/main`; a squash
  makes `merge-base --is-ancestor` return false forever, so ancestry is never the check.

- 🔴 **THE CLAUSE-2 CHECK I SHIPPED IN THIS DOC WAS A FALSE POSITIVE, CAUGHT MINUTES AFTER PUSHING
  IT — the arc's own lesson, walked into while writing the arc's closing handoff.** The check was
  `grep -n 'size-ratchet' <SKILL.md> | grep -c 'prune'`, and it returns **1** while the clause is
  **OPEN**. `SKILL.md` step 5 is ONE ENORMOUS PHYSICAL LINE holding every refusal status separated
  by `·`, and its trailing pointer reads *"📖 every status, the bucket rules and `--prune`:
  …/write-gate.md"*. So the line contains both tokens, and a line-scoped grep cannot tell the
  `size-ratchet` REMEDY from a pointer 2,000 characters further along. It returned **0** earlier in
  the same session only because that pointer had not yet gained `--prune` (added by `dc159b07` /
  `f1801784`) — i.e. the check broke when an UNRELATED edit moved text onto the same line.
  **The correct check splits the line into its fields first:**
  `git show origin/main:claude/skills/handoff/SKILL.md | tr '·' '\n' | grep -F '`size-ratchet`' | grep -c -- '--prune'`
  → **0 while the clause is open**, against the naive form's 1. 🔴 **The general rule, and it is
  the same one this whole arc is about: a grep is scoped to a LINE, so on a file whose "lines" are
  paragraphs, line scope is not statement scope.** Split to the real delimiter before counting, and
  when a check's answer flips without the thing it measures changing, suspect the check.

- 🔴 **A RANKED ITEM'S BYTE BUDGET IS A MEASUREMENT WITH A SHELF LIFE, AND RE-MEASURING IT
  DISSOLVED THE FORK IT WAS BLOCKING ON.** Item 1 was carried across sessions as an operator
  decision — raise `MAX_BYTES` or amend the closing condition — because **87 B** of headroom
  could not hold ~144 B of wiring. That figure was measured at `dc159b07` and was already
  wrong when it was written down as a blocker: `f1801784` shrank `SKILL.md` by 182 B, leaving
  **269 B**. The wiring cost 150 B and fit with 119 B to spare. **Nothing was decided, nothing
  was raised, and the item was never actually blocked.** The general tell: a ranked item whose
  forcing argument is a NUMBER, carried forward from a previous session. Re-derive the number
  before accepting that it blocks anything — `git show <ref>:<path> | wc -c` costs one command
  against an operator round-trip. Same family as this arc's own headline (a refusal is a claim
  with a shelf life); here the stale claim was in the HANDOFF, not the code.
- 🔴 **MY NEGATIVE CONTROL RED ITSELF ON A THRESHOLD I TOOK FROM PROSE, AND THAT IS THE
  CONTROL WORKING.** The locator control asserted `len(line) > 2_000` to pin "step 5 is one
  paragraph-line". The real line is **1,288** characters — I had lifted "2,000 characters"
  from this doc's own approximate phrasing (*"a pointer 2,000 characters further along"*) and
  hard-coded it as a bound. It failed with its own message on the first run. **The fix was not
  to retune the number: it was to stop asserting a size at all.** The claim is STRUCTURAL —
  many fields share one physical line, and the flag appears on it in a field OTHER than this
  one — so it is now `line.count("·") >= 5` plus `any(PRUNE_FLAG in f for f in others)`, where
  `others` is the raw `·` split minus the `size-ratchet` field. 🔴 **An intermediate draft was
  worse than either: `PRUNE_FLAG in line.replace(field, "")` — `field` is whitespace-NORMALISED
  and the line is not, so the `replace` is a no-op and the assertion passes vacuously in both
  arms.** A control that compares normalised text against raw text is not a control. **When a
  guard needs a magic number, ask whether the thing you mean is a shape.**
- 🔴 **A LINE-SCOPED MUTANT PROVES THE TWO GUARDS COVER DIFFERENT PROPERTIES, AND IT IS THE
  ONLY MUTATION THAT DOES.** Reverting `_size_ratchet_field` from the `·` split back to
  `doc.splitlines()` — i.e. reinstating exactly the false positive that shipped as this arc's
  closing condition — leaves the REMEDY test **GREEN** (the line contains `--prune` via the
  trailing pointer) and reds only the locator control. Reverting the SKILL.md field reds only
  the remedy test. **Neither guard subsumes the other, and a single mutation would have
  "proved" whichever one it happened to hit.** Matrix, every mutation isolated to the narrowest
  expression that can be wrong, each **1 failed / 1 passed**: field→base ⇒ remedy RED with its
  own message · `--prune` moved AFTER the override ⇒ the ORDERING assert RED alone · locator→line
  scope ⇒ control RED, remedy GREEN. Then six suites at HEAD ⇒ 887 passed.
- ⚠ **I EDITED THE PRIMARY CLONE INSTEAD OF THE WORKTREE, AND THE DIFF STAT IS WHAT CAUGHT
  IT.** The `Edit` call carried `/home/zach/workspace/devrc/...` rather than the worktree path —
  an easy slip once the worktree exists, because every READ up to that point had legitimately
  been from the primary clone. **The recovery is safe only because the clone was clean on that
  path: `git diff --stat` showed `87 insertions(+)` and `git diff | grep -c '^-'` showed 1, i.e.
  exactly my own block and nothing else.** Then `git diff -- <path> > patch`,
  `git -C "$WT" apply --check` (it applies clean), apply, and `git -C $D restore -- <path>`.
  🔴 **Check the diff is ONLY yours BEFORE restoring** — `restore` on a path carrying someone
  else's WIP destroys it silently, and the primary clone is exactly where other sessions' WIP
  lives. The tell that the edit went to the wrong tree at all was the post-tool hook naming a
  file from an unrelated effort as newly staged.
- ⚠ **A `grep -F` pattern with BACKSLASH-ESCAPED backticks inside single quotes matches
  nothing, and the zero reads as a regression.** Verifying a restore, `grep -F
  '\`size-ratchet\`'` returned **0** and briefly looked like the restore had failed. Inside
  single quotes the backslashes are literal, so the pattern was `\`size-ratchet\`` — absent from
  the file. `cmp -s` against the saved copy settled it in one command and cannot be spelled
  wrong. **Prefer `cmp`/`sha256sum` over a grep count when the question is "is this file the
  bytes I meant".** Same family as this repo's grep gotchas: the answer was a fact about the
  pattern, not about the file.
- ⚠ **`cairn-ops/write.sh append` BREAKS ITS OWN POST-WRITE CHECK ON A LARGE `--text`, and the
  failure reads as a store problem.** A 1,926-character bullet (cap is 2,000) appended fine —
  the revision came back and the text reads back — but the automatic verification then printed
  `common.sh: line 131: .../sed: Argument list too long`, `.../head: Argument list too long`,
  `the reader would not name a store for scope 'devrc'`, and `NOTHING WAS CHECKED … exit 22`,
  which `write.sh` reports as **the write LANDED but is UNCONFIRMED**. The store was fine:
  `cairn routes` resolves `devrc -> personal` and a direct `cairn validate --scope devrc` exits
  **0** with `36 of 36 entry file(s) parse, 0 malformed`, clean entry shape, 0 dropped lines and
  0 out-of-reach markers. So the exit 22 was the WRAPPER's own argv, inherited from the giant
  `--text`, not a routing or format change. 🔴 **Follow the protocol anyway — do NOT retry the
  append** (it is content-hashed, but the instruction exists because a retry after a real 8 is
  a clobber); confirm by reading the entry back and running `cairn validate --scope <scope>`
  directly. ⚠ **And my own `rc=` was a lie in the same breath**: `… | tail -20; echo rc=$?`
  reports `tail`'s status, so a wrapper exiting 22 printed `rc=0`. Capture the status before
  piping.
- ⚠ **An adjacent present-tense byte figure was false by 17×.** `SKILL_PINS` in
  `scripts/tests/test_handoff_doc.py` justified a pin with *"SKILL.md sits at 7 B of headroom"*
  against a real 119 B. Converted to past tense with the reason stated, because that figure moves
  on every edit to the file and no test watches it — the same claim-with-a-shelf-life class as the
  refusal that opened the arc, sitting in the file that guards against it.

- 🔴 **ONE PREFIX RELATION DEFEATED THREE GUARDS IN SUCCESSION, AND EACH FIX LOOKED COMPLETE.**
  `--prune` is a **prefix of** `--prune-count`, and the module requires the PAIR (`parse_args`
  exits `EXIT_USAGE` on one alone). So: (a) `assert PRUNE_FLAG in field` passes a field naming
  only `--prune-count <n>` — measured, deleting `--prune-count` left the module GREEN at **639
  passed**; (b) adding `assert PRUNE_COUNT_FLAG in field` still passes the MIRROR case, because
  `--prune` is a substring of the longer flag; (c) with presence finally closed by
  `rf"{flag}(?![-\w])"`, the ORDERING check was still `field.index(hd.PRUNE_FLAG)` — and a field
  naming `--prune-count` before the override with `--prune` only INSIDE the override clause gives
  index 72 < 130, real token at 237, **639 passed, rc 0**, executor routed to the override. And
  (d) the arc's own closing-condition grep had it too: `grep -c -- '--prune'` returns **1** on
  that same refused field. **Four sites, one root cause, and each fix was verified by a mutation
  in only ONE direction.** The lesson is the mirror: when a token is a prefix of another, every
  test of it needs BOTH deletion directions and a right-hand boundary — in the assertion, in the
  position lookup, and in the shell check.
- 🔴 **FOUR CONSECUTIVE VERSIONS OF ONE DOCSTRING MIS-ATTRIBUTED ITS OWN ASSERTIONS. THE CURE WAS
  A GUARD, NOT A FIFTH REWRITE.** Each version was written carefully by a round fixing the
  previous one, and each was measured false by the next: "three assertions" against six; a credit
  given to an assertion that cannot execute for the shape it named; an ordinal pointing at a live
  assertion as if deleted. `test_this_controls_docstring_numbering_matches_its_own_assertions`
  now walks the AST and fails when the docstring's stated count or its `1..N` ordinals disagree
  with the code. 🔴 **It went red on its FIRST run** — my own edit had left two intro sentences
  ("SIX assertions" above "FIVE assertions") and it read the stale one. **A count in prose is a
  claim; make it a checked one.** Keep such a guard deliberately NARROW (count and ordinals, not
  semantic attribution) or it becomes the too-wide docstring it exists to prevent.
- 🔴 **A GUARD CAN BE DOMINATED — NO REACHABLE CASE OF ITS OWN — AND FOUR ROUNDS WILL INVENT
  REASONS TO KEEP IT.** The locator's length bound was credited with catching a
  field-START-to-END-OF-LINE locator "which drags in no neighbour". Measured: `size-ratchet` sits
  at offset 293 on step 5's line and `leak-refused` at 615, so that shape is 1,186 chars and
  **contains the neighbour** — the scope control fires, not the bound. Reaching it needed a field
  starting with `size-ratchet`, excluding `leak-refused`, over 600 chars, against a
  field-to-neighbour span of 322. **Deleted, with all four dead rationales recorded at the site**,
  per RULES: if a guard has lost its reason, write that it has none — do not go looking for a
  better one. Verified after removal: all three locator shapes still red.
- 🔴 **A MUTANT THAT WAS ALREADY RED AT THE PRE-FIX TIP IS NOT EVIDENCE FOR THE FIX.** A commit of
  mine cited two shapes as proof of an ordering fix; one of them reded at the base too, so it
  discriminated nothing, and the shape that actually proved the new half (`--prune` before the
  override, `--prune-count` after — 2 passed at base, 1 failed at HEAD) was in neither the commit
  nor the PR body. **The property was guarded; the written evidence did not contain the case that
  proved it.** Check each cited mutant against the BASE before listing it as a control.
- 🔴 **A DEPLOY IS NOT THE CONSUMER, AND `readlink -f` IS THE ARBITER.** The closing condition
  greps `origin/main`, so it closes *"the repo says X"*, never *"an executor reads X"*.
  `~/.claude/skills/handoff/SKILL.md` resolves into `/nix/store/…-devrc-claude-skills/`, a
  `home.file` COPY — so the merged fix was invisible to every executor until a `home-manager
  switch`. **Record the store path, byte count and sha BEFORE the switch**; "switch exited 0" is
  a claim about the switch. ⚠ And read ship.sh's SUMMARY line, not its per-host ticks: the ticks
  print `✅ VERIFIED … + switched` before the line that says cross-host agreement was never
  compared.
- 🔴 **`audit-dispatch.py` RESOLVES BOTH THE REPO AND ITS GIT FROM CWD, AND THIS DIRECTORY IS A
  DISPATCH HUB.** Run from `datapacket-talos` against a devrc PR it fails
  `Could not resolve to a PullRequest` (fixed by `--repo owner/name`) — and even WITH `--repo` it
  cannot resolve the PR's shas, so the payload reading degrades to **`PAYLOAD NOT VERIFIED`** and
  the attribution gate silently falls back to the count you typed. **Assemble from a checkout of
  the target repo** (a worktree is fine) whenever the payload figure is load-bearing. Same class
  as this repo's gotcha #20.
- ⚠ **A `round=0` `audit-claims` block is REFUSED by design** — round 0 fixes nothing, so
  anchoring a delta on it would attribute the whole change to a round that changed no code. Round
  0's verdict goes in a PR comment as prose, and its **dispositions ride the first FIXING round's
  block** (`--dispositions D1=…,D2=…`). ⚠ Commas inside a disposition REASON are parsed as token
  separators — the tail is reported as an unreadable token in every later brief. Write the reason
  without commas.
- ⚠ **The attribution gate fires on TWO CONSECUTIVE payload-zero rounds and exits 5** — a
  mechanical stop, not a judgement, and it is the correct end for a ladder whose rounds are
  fixing only the scaffolding earlier rounds wrote. Watch it fire rather than asserting the
  condition is met: this session's rounds 2 and 3 were both payload-zero and the refusal printed
  its own reasoning, citing two measured precedents where the condition was met *and stated in
  writing* and the ladder ran nine and twenty-one more rounds anyway.
- ⚠ **`cairn-ops/write.sh append` BREAKS ITS OWN POST-WRITE CHECK ON A LARGE `--text`.** A 1,926
  char bullet (cap 2,000) appended fine, then verification printed
  `common.sh: line 131: .../sed: Argument list too long` and `NOTHING WAS CHECKED … exit 22`,
  reported as *the write LANDED but is UNCONFIRMED*. The store was fine — `cairn routes` resolves
  the scope and a direct `cairn validate --scope devrc` exits 0 with `36 of 36 entry file(s)
  parse`. Follow the protocol anyway (do NOT retry the append); confirm by reading the entry back
  and validating directly.
- ⚠ **`… | tail -N; echo rc=$?` reports `tail`'s status.** A wrapper exiting 22 printed `rc=0`
  this session. Capture the status before piping — gotcha #8, hit while reading a tool's verdict.
- ⚠ **A `grep -F` pattern with BACKSLASH-ESCAPED backticks inside single quotes matches nothing**,
  and the zero reads as a regression. `grep -F '\`size-ratchet\`'` returned 0 on a file that
  contained it. Use `cmp`/`sha256sum` when the question is "is this file the bytes I meant".

## How to verify
```bash
D=/home/zach/workspace/devrc
# 1. THE ARC'S CLOSING CONDITION — 1 = closed. 🔴 BOTH corrections are load-bearing:
#    SPLIT on `·` first (step 5 is one physical line whose trailing pointer names --prune, so a
#    line-scoped grep returns 1 either way), AND match a right-hand boundary (--prune is a PREFIX
#    of --prune-count, so a bare grep returns 1 on a field naming only the longer flag, which the
#    tool REFUSES). The originally-registered check had BOTH defects.
git -C $D show origin/main:claude/skills/handoff/SKILL.md \
  | tr '·' '\n' | grep -F '`size-ratchet`' | grep -cE -- '--prune($|[^-[:alnum:]])'   # 1
# 2. 🔴 THE CONSUMER — what an executor actually reads. Distinct from (1) and NOT implied by it.
readlink -f ~/.claude/skills/handoff/SKILL.md          # /nix/store/…-devrc-claude-skills/…
tr '·' '\n' < ~/.claude/skills/handoff/SKILL.md | grep -F '`size-ratchet`' \
  | grep -cE -- '--prune($|[^-[:alnum:]])'             # 1  (0 ⇒ this host needs a switch)
# 3. the three guards reached main
git -C $D show origin/main:scripts/tests/test_handoff_doc.py | grep -c \
  -e 'test_the_size_ratchet_remedy_routes_to_the_prune_EXIT_not_the_override' \
  -e 'test_the_size_ratchet_locator_reads_a_FIELD_not_the_whole_line' \
  -e 'test_this_controls_docstring_numbering_matches_its_own_assertions'
# 4. no raise happened
git -C $D show origin/main:scripts/tests/test_handoff_skill_size.py \
  | grep -E '^(MAX_BYTES|MIN_HEADROOM_BYTES) ='        # 21_200 / 900
git -C $D show origin/main:claude/skills/handoff/SKILL.md | wc -c   # 20222 (78 B slack)
# 5. the suites — EXACTLY these six sum to 888; a different plausible six gives 1,021
nix develop "$D" --command bash -c "cd $D && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  scripts/tests/test_handoff_doc.py scripts/tests/test_handoff_skill_size.py \
  scripts/tests/test_skill_audit.py scripts/tests/test_run_tests_targets.py \
  scripts/tests/test_no_public_ips.py scripts/tests/test_doc_path_rot.py -q"
# 6. the guards are not vacuous — in a worktree, revert ONE at a time (each 1 failed / 1 passed):
#      drop `--prune-count <n>` from the field        -> remedy RED   (was 639 passed)
#      drop `--prune`, keep `--prune-count`           -> remedy RED   (was 2 passed)
#      `--prune` only INSIDE the override clause      -> ordering RED (was 639 passed)
#      locator -> doc.splitlines()                    -> control RED
#      docstring count FIVE -> FOUR                   -> numbering guard RED
#      add an assertion, leave the count              -> numbering guard RED
#      drop the whole override clause                 -> five-site WHO_MAY pin RED
# 7. the ladder is over, mechanically — expect exit 5, "THE ATTRIBUTION GATE HAS FIRED"
(cd $D && python3 scripts/audit-dispatch.py 1943 --repo innovation-upstream/devrc --round 4)
```
🔴 **(1) and (2) are different claims and (1) does not imply (2).** `~/.claude/skills` is a
`home.file` store copy, so `origin/main` can say the right thing while every executor on a host
reads the old field. Run both, per host.
