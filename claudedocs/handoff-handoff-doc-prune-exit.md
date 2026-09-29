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
- **`devrc` `main` @ `79a9b22a`**; the base clone is 4 behind and carries one untracked file
  (`claudedocs/scope-chief-model-selector-2026-09-19.md`) that is **not mine** — leave it.
- **#1916 MERGED as `3cdf8a8f`** — `--prune FILE --prune-count N` on `handoff_doc.py`.
  Refusals are `status=prune-refused`, **exit 15**, one marker per cause
  (`[count mismatch]` `[absent]` `[ambiguous]` `[load-bearing]: <key>` `[section heading]`
  `[partial block]` `[fence delimiter]` `[replace section]`). Scoped to the three append-only
  sections; four fields can never be pruned (`clawgate-task:` `closing-condition:` `forcing:`
  `as-of:`) with no override, except `as-of:` when its whole enclosing `###` block is named.
  🔴 **The CODE IS LIVE with no switch** — `readlink -f scripts/lib/handoff_doc.py` terminates
  **in the repo**, so it went live when the base clone fast-forwarded.
- 🔴 **I EXERCISED IT ON A REAL DOCUMENT and it worked** — the author's own stated gap was
  "all fixtures". Proposal run against `jev-ui-demo`'s handoff removing a 10-line `###` block:
  `status=proposed`, nothing written, correct diff, and it exercised the `as-of:` exception.
  **It also fired the durable-content warning correctly** — the block's last line was a real
  instruction the surviving sibling did not carry, so **I did not land that prune.** First time
  that warning has fired on a non-fixture doc.
- **#1919 OPEN at `4243755b`, CI GREEN** — all four Tekton statuses `success`,
  `collected=24694 passed=24687 skipped=7 failed=0`. Ready to merge. It corrects `write-gate.md`
  §C, which claimed **one** emphasis spelling is admitted for a `forcing:` field, and adds a
  guard. `claude/skills/handoff/SKILL.md` is **untouched** by it.
- **Pre-push verification of #1919 in the correct devShell: 781 passed, 0 failed** across
  `test_handoff_doc.py` + `test_skill_audit.py` + `test_run_tests_targets.py`.
- 🔴 **I verified the new guard's mutation kill MYSELF rather than accepting the report.**
  `_MARKUP` `{0,3}` -> `{0,2}` reds **two** tests with the guard's own message —
  *"§C's ledger says 'forcing***: gate' parses to 'gate'; the live `_FORCING` gives None"* — one
  structural, one behavioural through `ranked_items`, the real consumer. 2 failed / 631 passed.
  Isolated needle (count asserted 1), `.git` removed from the `cp -a` copy, restore verified
  byte-identical (`41d089d203c2634a`), green again after.
- 🔴 **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5** (NOTHING
  RESOLVED). An unknown session id answers 200 with an empty array, so that zero cannot
  distinguish "touched no task" from "wrong id". **Not a clean bill of health.**
- **Not this arc, recorded so nobody re-derives it:** `jev-ui-demo` has its own current doc
  (`claudedocs/handoff-jev-ui-demo.md` @ `a756766`) carrying four merged PRs, a deployed and
  twice-verified build, and an unauthorised paid sweep. Do not fold it in here.

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
1. **Merge devrc#1919.** CI is green at `4243755b`, round 0 ran and sent it back once, the rework
   addressed all four items, and I independently re-verified the mutation kill and the pre-push
   suite. Nothing is outstanding on it. `IN FLIGHT: innovation-upstream/devrc#1919`.
    forcing: user — the operator said to land the correction, then to run round 0, then proceed.
2. **Decide the `MAX_BYTES` raise, then wire step 5 to reach for `--prune`.** This is what makes
   #1916 reachable: today `size-ratchet`'s only named remedy is `--override-size-ratchet`, which
   ships the over-ceiling doc. The wiring costs ~**144 B**; `claude/skills/handoff/SKILL.md` has
   **25 B** against its enforced budget (`MAX_BYTES 21_200` − `MIN_HEADROOM_BYTES 900` = 20,300,
   file 20,275 at the time — 🔴 **re-measure, `main` moved the file to 20,213 B under me**).
   🔴 **The obvious trim is REFUTED, do not retry it:** demoting the emphasis/near-miss variant
   catalogue out of `SKILL.md` reds **three** `TestSkillAndModuleAgree` guards, by mutation — they
   pin those exact phrases because the skill is the only copy an executor reads at step 5. So the
   remaining path is a deliberate raise to ~21,400, recorded in
   `scripts/tests/test_handoff_skill_size.py` which owns the constant. **Operator's call; I did
   not take it.** Files: that test + `claude/skills/handoff/SKILL.md`.
    forcing: none
3. **File the browser-skill knife-edge.** `scripts/browser-bridge/SKILL.md` is 12,020 B against a
   12,038 B enforced budget — **18 B** — and it has already blown that budget once (#1917), which
   is what reddened two unrelated PRs for an hour. Needs an issue with a closing condition.
    forcing: none

## Defects (batched)
- **`scripts/browser-bridge/SKILL.md` has 18 B of headroom** (12,020 / 12,038) and blew its
  budget once on 2026-09-29, reddening PRs #1919 and #1922 for ~1 h. Ranked item 3.
- **No pre-push hook is installed in this clone** — `core.hooksPath` unset, no
  `.git/hooks/pre-push`, only `prepare-commit-msg`. devrc's documented synchronous gate never
  ran on either push this session. Nothing was bypassed; it is simply absent.
- **`scripts/gate.sh` cannot print PASS off a scoped run** (exit 91), so no tier verdict was
  produced for #1919; two attempts were killed at 40 and 60 minutes against other sessions' full
  suites. In-cluster CI is the gate that actually ran.
- **A PR body of mine carried two false claims, both corrected in the rework**: "four guards" was
  **three**, and "81 pinned phrases / 2,050 B" was not reproducible — the strict inventory is
  **46 / 1,468 B** by the rework's hand-read count, against round 0's **23**. 🔴 That quantity has
  now been measured three times with three answers; **do not let any count of it become
  load-bearing.**

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

## How to verify
```bash
D=/home/zach/workspace/devrc
# 1. --prune exists and is LIVE (readlink must terminate IN the repo, not /nix/store)
readlink -f "$D/scripts/lib/handoff_doc.py"
python3 "$D/scripts/lib/handoff_doc.py" --help | grep -A1 -- --prune
# 2. #1919's guard, in the CORRECT environment
nix develop "$D" --command bash -c "cd $D && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  scripts/tests/test_handoff_doc.py -k EmphasisRuleByPOSITION -q"
# 3. the guard is not vacuous: break it and watch ITS OWN message
#    _MARKUP {0,3} -> {0,2} in scripts/lib/handoff_doc.py, in a `cp -a` copy with .git removed,
#    expect 2 failed / 631 passed and "§C's ledger says ... the live `_FORCING` gives None"
# 4. CI, SHA-pinned — never `gh pr checks`, which re-resolves the sha at call time
SHA=$(gh pr view 1919 --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)
gh api "repos/innovation-upstream/devrc/commits/$SHA/status" \
  --jq '[.statuses[]|"\(.context)=\(.state)"]|sort|join("  ")'
```
🔴 **Expect FOUR statuses and read all four** — `devrc-{pytests,gotests,nodetests,cairn-client-runs}`.
A PR's contexts have no `-main-` infix; `main`'s do, and they are a different gate.
