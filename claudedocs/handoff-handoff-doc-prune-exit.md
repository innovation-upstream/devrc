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
- **`devrc` `origin/main` @ `3cfaca85`**; the base clone is 7 behind and carries two untracked
  `claudedocs/scope-chief-*.md` files that are **not mine** — leave them.
- **#1919 MERGED as `643fd0ef`** (19:51:28Z, by ZacxDev). Verified **by content on
  `origin/main`**, not by the PR's state: `write-gate.md` carries the POSITION grid, the
  negative lookbehind and the `_MARKUP` bounds; `scripts/tests/mutants-handoff-cap.sh` and the
  new test class are present. Ancestry happened to hold too, so this one was not a squash.
- **#1926 OPEN at `12e1d611`** — `fix/size-ratchet-names-the-prune-exit`. All four Tekton
  statuses registered and `pending` at the time of writing. 🔴 **The first SHA-pinned read
  returned `n=0`, and that is the unregistered-rollup case, not a result** — the floor here is
  4, never 0.
- **The `MAX_BYTES` decision was PUT TO THE OPERATOR and ANSWERED: lever 1.** Fix the tool
  message; do **not** raise the ceiling; leave `claude/skills/handoff/SKILL.md` untouched.
- **What #1926 does.** Rule (p)'s `size-ratchet` refusal ended remedy 2 with *"`Gotchas` and
  `Open investigations` APPEND here, so this tool cannot shrink them for you."* That was true
  when written and **rule (q) falsified it in #1916**, so an author whose overage sat in an
  append-only section was routed to `--override-size-ratchet`, which **ships the over-ceiling
  doc**. Remedy 2 is now the prune route (flags read off `PRUNE_FLAG`/`PRUNE_COUNT_FLAG`);
  MOVE became remedy 3, keeping its real function and losing the false half.
- 🔴 **The arc still CANNOT close, and lever 1 is why.** The closing condition's second clause
  wants `--prune` on `SKILL.md`'s `size-ratchet` line; lever 1 deliberately does not touch that
  file. Either the raise happens later or the condition gets amended — ranked item 2.
- **Re-measured, and the previous doc's figures had MOVED**: `claude/skills/handoff/SKILL.md` is
  **20,213 B** against an enforced budget of **20,300** (`MAX_BYTES 21_200` −
  `MIN_HEADROOM_BYTES 900`) = **87 B** of headroom, not the 25 B recorded. `--prune` appears
  **once** in that file, in the trailing `📖` pointer — **not** on the `size-ratchet` line.
- **Carried forward, still true, do not re-derive:**
  - **#1916 MERGED as `3cdf8a8f`** — `--prune FILE --prune-count N`. Refusals are
    `status=prune-refused`, **exit 15**, one marker per cause (`[count mismatch]` `[absent]`
    `[ambiguous]` `[load-bearing]: <key>` `[section heading]` `[partial block]`
    `[fence delimiter]` `[replace section]`). Scoped to the three append-only sections; four
    fields can never be pruned (`clawgate-task:` `closing-condition:` `forcing:` `as-of:`) with
    no override, except `as-of:` when its whole enclosing `###` block is named.
  - 🔴 **The code is LIVE with no switch** — `readlink -f scripts/lib/handoff_doc.py` terminates
    **in the repo**, so it went live when the base clone fast-forwarded.
  - **`--prune` has been exercised on a real document**, not only fixtures, and its
    durable-content warning fired correctly on a non-fixture doc; that prune was not landed.
  - **Not this arc:** `jev-ui-demo` has its own current doc
    (`claudedocs/handoff-jev-ui-demo.md`). Do not fold it in here.
- 🔴 **No `clawgate-task:` field again this session**: `clawgate_handoff.sh resolve` exited **5**
  (NOTHING RESOLVED). An unknown session id answers 200 with an empty array, so that zero cannot
  distinguish "touched no task" from "wrong id". **Not a clean bill of health.**

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
1. **Merge devrc#1926 once its four statuses settle.** Read them SHA-pinned off
   `repos/innovation-upstream/devrc/commits/<sha>/status`, never `gh pr checks`, and require
   **four terminal** conclusions — an empty or two-status rollup settles instantly and
   correctly-looking. `IN FLIGHT: innovation-upstream/devrc#1926`. Files:
   `scripts/lib/handoff_doc.py`, `scripts/tests/test_handoff_doc.py`.
    forcing: user — the operator chose lever 1 this session and the work is the answer to it.
2. **Resolve the arc's second clause: raise `MAX_BYTES`, or amend the closing condition to
   match lever 1.** These are the only two ways this arc closes, and picking is the operator's.
   The raise is ~21,200 → ~21,400 in `scripts/tests/test_handoff_skill_size.py` (which owns the
   constant and wants a ledger entry naming the instruction that would not fit), plus ~144 B of
   wiring into `claude/skills/handoff/SKILL.md` against 87 B of headroom. 🔴 **Against it:** that
   file's own ledger says the answer to the next round is *"lever 1 or 2, not another raise"*,
   and it last **ratcheted DOWN**. 🔴 **The obvious trim is REFUTED — do not retry it:** demoting
   the emphasis/near-miss catalogue out of `SKILL.md` reds **three** `TestSkillAndModuleAgree`
   guards, by mutation.
    forcing: none
3. **File the browser-skill knife-edge.** `scripts/browser-bridge/SKILL.md` is **12,020 B**
   (re-measured at `3cfaca85`, unchanged) against a **12,038 B** enforced budget
   (`MAX_BYTES = 12_288` in `scripts/browser-bridge/tests/test_skill_size.py`, minus 250) —
   **18 B**. It has already blown that budget once (#1917), reddening two unrelated PRs for an
   hour. Needs an issue with a closing condition.
    forcing: none

## Defects (batched)
- **`scripts/browser-bridge/SKILL.md` has 18 B of headroom** (12,020 / 12,038, re-measured at
  `3cfaca85`) and blew its budget once on 2026-09-29, reddening PRs #1919 and #1922 for ~1 h.
  Ranked item 3.
- **No pre-push hook is installed in this clone** — re-checked at `3cfaca85`: `core.hooksPath`
  unset, `.git/hooks/` holds only `prepare-commit-msg`. devrc's documented synchronous gate has
  not run on any push from here. Nothing is being bypassed; it is simply absent.
- **`scripts/gate.sh` cannot print PASS off a scoped run** (exit 91), so it produced no tier
  verdict for #1919 and none for #1926 either. In-cluster CI is the gate that actually runs.
- ✅ **CLOSED:** the two false claims in an earlier PR body ("four guards" was three; the
  "81 pinned phrases / 2,050 B" figure was not reproducible) were corrected in that PR's rework.
  🔴 The phrase count was measured three times with three answers — **do not let any count of it
  become load-bearing.**

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

## How to verify
```bash
D=/home/zach/workspace/devrc
R=innovation-upstream/devrc
# 1. #1919 landed — by CONTENT, never by the PR's state
git -C $D show origin/main:claude/skills/handoff/reference/write-gate.md | grep -c 'GRID, NOT A LIST'
git -C $D cat-file -e origin/main:scripts/tests/mutants-handoff-cap.sh && echo present
# 2. #1926's guards, in the CORRECT environment (devrc's devShell — another repo's venv LIES)
nix develop "$D" --command bash -c "cd $D && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  scripts/tests/test_handoff_doc.py -k 'ALL_THREE_ways_out or does_not_deny_the_exit' -q"
# 3. the guards are not vacuous — ISOLATE the mutation, do not just revert the file:
#    in a `cp -a` copy with .git REMOVED, change only the --prune-count token in the remedy-2
#    string; expect 1 failed / 1 passed and the assertion's OWN message.
# 4. CI, SHA-pinned — never `gh pr checks`, which re-resolves the sha at call time
SHA=$(gh pr view 1926 --repo $R --json headRefOid --jq .headRefOid)
gh api "repos/$R/commits/$SHA/status" --jq '"n=\(.statuses|length) "+([.statuses[]|"\(.context)=\(.state)"]|sort|join("  "))'
# 5. the arc's OPEN clause: --prune is still absent from SKILL.md's size-ratchet line
git -C $D show origin/main:claude/skills/handoff/SKILL.md | grep -n 'size-ratchet' | grep -c 'prune'
```
🔴 **Expect FOUR statuses at step 4 and read all four** — `devrc-{pytests,gotests,nodetests,cairn-client-runs}`.
A PR's contexts have no `-main-` infix; `main`'s do, and they are a different gate. **`n=0` is the
unregistered rollup, not a verdict.** Step 5 returning **0** is the arc's second clause still open.
