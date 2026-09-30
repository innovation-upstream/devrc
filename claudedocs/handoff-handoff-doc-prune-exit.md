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
- **`devrc` `origin/main` @ `7f84306c`.** **#1919 MERGED** as `643fd0ef` (verified by content, not
  by PR state). **#1926 OPEN at `c8d06ba5`, CI GREEN on all four Tekton statuses.**
- **#1926 is the lever-1 answer the operator gave**, and it has run a **four-round audit ladder**
  (0, 1, 2, 3 — round 4 dispatched). 🔴 **EVERY ROUND FOUND MY OWN PROSE ASSERTING SOMETHING
  FALSE**, and none of them found a behaviour defect:
  - **r0** — a guard docstring claimed the sibling would catch "a reword that keeps the denial";
    measured FALSE, the reword passed both guards. Also: the standalone MOVE remedy was compliable
    only by the hand edit rule (q) exists to remove, and rule (p)'s own docstring still carried the
    falsified claim.
  - **r1** — the refusal asserted eviction needed "its own run … its own commit". FALSE: `--prune`
    COMBINES with `--update` (`--help`; and `main()` prunes `merged_text` before rule (p) reads it).
    The instruction was harmful — a prune-only first commit cannot carry the pointer, so step 1 is
    a pointerless deletion. Both retracted sentences were also still live in `write-gate.md` §I.
  - **r2** — the "positive control" controlled nothing: gutting the sweep to `for label, path in ():`
    left BOTH tests green. Plus `#`-wrapped copies invisible, and `"ENDED"` matching inside
    **"appended"**.
  - **r3** — rule (q)'s header still said, in the present tense, that no path could remove a line —
    **two lines above my own note claiming the conversion was made.** And the r2 normaliser fix
    covered `#` comments but not string literals, i.e. the copy nobody reads, not the copy an agent
    does (916 lines of that module end in a bare closing quote).
- 🔴 **THE FIX FOR A VACUOUS GUARD WAS ITSELF VACUOUS, BY THE SAME MUTATION.** My first r2 remedy
  asserted `_rule_p_sites()` was non-empty — pinning the FUNCTION while the mutation is at the CALL
  SITE. The mutant passed 4/4. What works is a positive control in the sweep's OWN body counting
  files read and occurrences seen. **Report the pair, never the zero alone.**
- **`--prune` (#1916) is now exercised end-to-end on a real document** — `jev-ui-demo` PR #14, one
  run, `--update` + `--prune`, pointer appended and 42 lines removed, doc 65,495 → 63,821 B. That
  is the evidence that #1926's corrected remedy text is FOLLOWABLE, which no test can assert.
- 🔴 **The arc still cannot close.** Clause 2 wants `--prune` on `SKILL.md`'s `size-ratchet` line;
  lever 1 deliberately does not touch that file. `SKILL.md` is **20,213 B** against a **20,300 B**
  enforced budget — **87 B**.
- 🔴 **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5**. An unknown session
  id answers 200 with an empty array, so that zero cannot distinguish "touched no task" from "wrong
  id". **Not a clean bill of health.**

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
1. **Merge devrc#1926 once round 4 reports**, regardless of whether it is clean — operator's
   standing call. `IN FLIGHT: innovation-upstream/devrc#1926` @ `c8d06ba5`, CI green. Then
   re-run the `/handoff` write on any doc to confirm the corrected refusal renders.
    forcing: user — the operator directed the ladder and set merge-after-round-4.
2. **Resolve the arc's clause 2: raise `MAX_BYTES`, or amend the closing condition to match
   lever 1.** Unchanged and still the only two ways this arc closes. 87 B of headroom against
   ~144 B of wiring. 🔴 The obvious trim is REFUTED — demoting the emphasis catalogue reds three
   `TestSkillAndModuleAgree` guards, by mutation.
    forcing: none
3. **Decide `jev-ui-demo`'s doc budget, then close PR #14.** Round 0 of that PR found
   `handoff_budget.GRANDFATHERED` holds **71 foreign entries** at allowances up to **425,984 B**,
   and that doc is bound by the bare 65,536 **only because `jev-ui-demo` is not in
   `handoff_index.REPO_ENV_HANDLES`** — so it was never in the population that block was measured
   over. One ledger line would have been the precedented treatment and would have made #14
   unnecessary. Files: `scripts/lib/handoff_budget.py`, `scripts/lib/handoff_index.py`.
    forcing: none

## Defects (batched)
- **`scripts/browser-bridge/SKILL.md` has 18 B of headroom** (12,020 / 12,038) and blew its budget
  once on 2026-09-29, reddening two unrelated PRs for ~1 h. Moved here from the ranked list: an
  audit-style finding is a defect, not a rank.
- **No pre-push hook is installed in this clone** — `core.hooksPath` unset, only
  `prepare-commit-msg`. devrc's documented synchronous gate has not run on any push from here.
- **`scripts/gate.sh` cannot print PASS off a scoped run** (exit 91) — no tier verdict for any
  round of #1926. In-cluster CI is the gate that actually ran.
- **The sandbox `nix build` tier was never verified for #1926** — rounds 2 and 3 both killed it
  unfinished rather than leak load. Every green on that PR is the dev-host pytest tier.
- 🔴 **#1926's payload is now comment-only for two consecutive rounds** — r2 25 payload / 0
  executable, r3 6 / 0, against 229 and 75 scaffolding lines. The attribution gate cannot fire
  (the range carries executable scaffolding), but that is the shape it exists to catch.

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

## How to verify
```bash
D=/home/zach/workspace/devrc
R=innovation-upstream/devrc
# 1. the guards, in the CORRECT environment (another repo's venv reports failures that do not exist)
nix develop "$D" --command bash -c "cd $D && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  scripts/tests/test_handoff_doc.py -k 'still_ASSERTS or actually_READS or DETECTOR_can_both' -q"
# 2. the sweep is not vacuous — round 2's mutation must RED with the sweep's own message
#    in a `cp -a` copy with .git removed:  for label, path in _rule_p_sites():  ->  in ():
#    expect "scanned 0 file(s) and saw 0 occurrence(s)"
# 3. the normaliser reaches STRING LITERALS, not just comments — plant the phrase split across
#    two adjacent literals in handoff_doc.py; it must be caught (it was seen=0 before round 4)
# 4. CI, SHA-pinned — never `gh pr checks`, which re-resolves the sha at call time
SHA=$(gh pr view 1926 --repo $R --json headRefOid --jq .headRefOid)
gh api "repos/$R/commits/$SHA/status" --jq '"n=\(.statuses|length) "+([.statuses[]|"\(.context)=\(.state)"]|sort|join("  "))'
# 5. the arc's OPEN clause: --prune still absent from SKILL.md's size-ratchet line
git -C $D show origin/main:claude/skills/handoff/SKILL.md | grep -n 'size-ratchet' | grep -c 'prune'
```
🔴 **Expect FOUR statuses at step 4** — `n=0` is an unregistered rollup, not a verdict. Step 5
returning **0** is the arc's second clause still open.
