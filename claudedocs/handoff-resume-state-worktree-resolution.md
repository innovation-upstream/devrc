# Handoff: resume-state-worktree-resolution — 2026-09-01

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

🔴 **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **6** — one task
(#440) is linked to this session with `role=read` and it is about an unrelated clawgate
deeplink. Filing or reading a task is not doing its work, so per the skill's no-worked rule
nothing was recorded. This says NOTHING about the board.

## Goal
Close **devrc#1164**: `scripts/resume-state.sh` given an explicit path to a handoff doc that
lives in a **linked worktree** reported `NO SUCH FILE`, fell back to newest-of-N, and emitted a
complete, confident digest reconciled against **a different initiative**. Handoff docs land in
linked worktrees by construction — `claude/RULES.md` makes worktree isolation the standing
default for any file-modifying agent — so this fires on the repo's own mandated workflow.

## State now
- **Branch `fix/resume-state-worktree-resolution` in `~/workspace/devrc-rsw`, commit
  `5d695c57`, PUSHED. No PR yet** (waiting on the sandbox tiers so its body carries measured
  numbers). Branched off `d86e5f81`. 4 files, +624/−69.
- **Part 1 — resolve into linked worktrees, scoped to the NAMED tree's own clone.** New
  `worktrees_holding <dir> <base>` enumerates `git -C <dir> worktree list --porcelain`;
  rc 0 + one path / rc 2 + N paths / rc 1 + nothing. 🔴 **The clone is an ARGUMENT, never
  `$PWD`** — for `<X>/claudedocs/<base>` it is `${dir%/claudedocs}`, and only the relative
  re-anchor uses `$root`. That preserves the invariant the file already carried: *an absolute
  token that is not on disk stays a miss, because serving a same-named doc out of whatever
  repo you happen to be standing in is the wrong-initiative bug in a different disguise.*
- **Part 2 — a named-missing handoff path no longer falls back.** The whole fallback chain is
  wrapped in `if [ -z "$named_missing" ]`, so `HANDOFF` stays empty and the EXISTING tested
  branch fires: `NOTHING was reconciled; the DRIFT section below is about no document at all.`
  🔴 **No exit code and no refusal were introduced** — the script has none today and always
  reports; adding one would be a contract change for every caller. 🔴 **Scoped to
  `named_missing`, NOT `unresolved`**: a bare basename is a slug, and the file records a
  MEASURED case where the fallback correctly served exactly that doc.
- **Ambiguity is not adjudicated.** 2+ worktrees holding the basename ⇒ nothing is chosen and
  the gap says how many and where. New rc 3 carries the token plus the candidate list.
- **VERIFIED BY ME, not taken from the implementing agent's report:**
  - regression matrix — **12 RED at `d86e5f81`, 16 GREEN at `5d695c57`**; the 4 that pass at
    both are the vacuity controls, which is correct.
  - `test_resume_state_handoff_resolution.py`: **165 passed** (151 at base, +14).
  - `bash -n` clean; `$'\n'` valid under the bash shebang; `amb`/`ambig` are live, not dead.
  - 🔴 **live end-to-end on real data** — same command, both revisions, using a doc that exists
    in exactly one linked worktree (`handoff-linux-cpu-profiling.md`, in `devrc-1136`):
    `d86e5f81` resolved `handoff-mention-detection.md` (a DIFFERENT initiative) with the
    NO SUCH FILE gap; `5d695c57` resolves the named doc and re-anchors
    `# repo: /home/zach/workspace/devrc-1136`.
- **The agent's mutation battery**: `mutation_battery_resume_state.py`, 56/56 killed, 14 new
  rows. **NOT independently re-run by me** — see Open investigations.
- **Two decisions the agent made that were not briefed, both kept.** The ambiguous enumeration
  is capped at 4 with `, and N more` (measured: the real clone has **140** linked worktrees and
  one basename appears in **28**; uncapped that is a ~2.5 KB single line) — the COUNT is never
  capped, and both sides of the threshold are pinned. And `claude/skills/resume/SKILL.md`
  carried a claim this change makes false (*"That gap means the digest is about a different
  initiative than you named"*, now true only of the slug class); it was corrected.
- 🔴 **Four EXISTING tests changed expectation** as a direct consequence of part 2 — they
  asserted the fallback that #1164 calls the bug. Re-read them before trusting the suite:
  `test_a_prose_path_that_does_NOT_exist_is_not_taken` (×2 params),
  `test_a_DIRECTORY_named_like_a_handoff_is_not_taken`, and two
  `test_every_gap_sentence_is_pinned_WHOLE` rows.
- **Claim held:** `resume-state-worktree-resolution`. Release when the PR merges.

## Open investigations — live diagnosis state
### The mutation battery's 56/56 is the agent's number, not mine
- **Symptom + exact repro:** not a failure — an unverified claim. `claude/RULES.md` says to
  re-verify an auditor's or subagent's self-reported mutation results, and I have not.
- **Observed (with values):** reported `56/56 killed, survived: none`, control 165 passed /
  0 failed, 14 new rows W1-W15 each with a named killer. The agent also reported updating
  M6, M7, M16, M22, M31 whose patterns its re-indent moved — **M16 specifically because the
  bare `if [ -z "$HANDOFF" ]` now occurs 3× and would otherwise report NOT APPLIED**, i.e. a
  silent survivor. That is the right failure mode to have caught, which raises confidence but
  is not verification.
- **Ruled out:** nothing yet.
- **Leading hypothesis:** the battery is sound; the risk is the ANCHOR class it just fixed
  (a pattern matching 0 or >1 times after a re-indent), not the logic.
- **Next probe:** `nix develop ~/workspace/devrc -c python3 scripts/tests/mutation_battery_resume_state.py`
  under `PYTHONDONTWRITEBYTECODE=1`, and grep the output for `NOT APPLIED` before reading any
  `killed` count.

## Next steps (ranked)
1. **Read the two sandbox tiers, then open the PR for `fix/resume-state-worktree-resolution`,
   then audit it.** Tiers were still building at handoff time; logs are
   `<scratchpad>/rsw-py.{out,err}` and `rsw-node.{out,err}`. Read each runner's own `RESULT:`
   line, never a piped exit code, and build the two derivations ONE AT A TIME (#1088).
   forcing: gate — `tekton/devrc-pytests` and `tekton/devrc-nodetests` both block the merge
   with `enforce_admins: true`.
2. **Re-run the mutation battery independently** before merging — see Open investigations.
   forcing: gate — the battery is the evidence the PR body will cite.
3. **After merge, `home-manager switch` (or `scripts/ship.sh`) — merging changes NOTHING here.**
   `scripts/resume-state.sh` and `claude/skills/resume/SKILL.md` are both nix-managed, so the
   deployed copies keep the OLD behaviour until a switch. `readlink -f` is the arbiter of
   whether a given path is live or a store copy.
   forcing: gate — the fix is inert on both hosts until this runs.
4. **Close devrc#1160** — four `status`→code associations `claude/skills/handoff/SKILL.md`
   documents in prose that nothing pins (`written`⇒0, `failed`⇒3, `push-failed`⇒3,
   behind-but-usable⇒0), plus a stale `MIN_TESTS` ledger comment. Note `SKILL.md` has **7 B**
   of headroom against its enforced 25,500, so normalising those forms is a byte-budget
   decision, not a test edit.
   forcing: none
5. **Apply the staged dnsmasq fix** — `sudo ~/workspace/devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`.
   Only the operator can run it.
   forcing: incident — measured 2026-08-29: the LAN router pins `registry-1.docker.io` with a
   487-day TTL and two of those IPs were reassigned to other AWS customers, so every
   `docker build` fails TLS. Worked around once with `--add-host`; unfixed.

## Gotchas / decisions / dead-ends
- 🔴 **`paste -sd' or '` DOES NOT JOIN WITH " or " — `-d` is a LIST OF CHARACTERS it cycles
  through**, so a 13-name join came out spliced with stray `o` and `r` and pytest rejected the
  `-k` expression. It failed loudly here; the same idiom silently produces a WRONG filter when
  the delimiters happen to be valid syntax. Use `awk 'NR==1{printf "%s",$0;next}{printf " or %s",$0}'`.
- 🔴 **A `-k` PATTERN THAT DOES NOT MATCH SILENTLY EXCLUDES THE TEST YOU MOST WANT RED.** My
  first red/green run used `-k 'named_missing_reconciles_NONE'`; the real name is
  `..._named_missing_handoff_reconciles_NONE_of_the_docs_present`, so the killing test was
  never selected and the run reported 1 red where the truth was 12. **Generate the selector
  from the actual `def test_` names in the diff — never type it from memory.**
- 🔴 **A REPRODUCTION CAN STOP REPRODUCING FOR A REASON UNRELATED TO THE FIX.** The original
  failing path (`devrc/claudedocs/handoff-handoff-doc-stale-base-guard.md`) now EXISTS,
  because #1146 merged and put that doc on `main` — so probing it proves nothing about
  #1164 either way. Check the fixture's premise still holds before reading the result; I
  had to go find a doc that lives in exactly one worktree to get a valid live test.
- **`cp -a` of a WORKTREE carries its `.git` POINTER FILE** — `rm -f <copy>/.git` immediately,
  and assert it is gone, before running anything git-shaped inside the copy.
- **`git worktree list` from a LINKED worktree lists the whole clone**, which is what makes
  `worktrees_holding` work from either side.

## How to verify
```bash
# 1. the regression matrix, re-derived (pre-fix script + post-fix tests)
T=$(mktemp -d); cp -a ~/workspace/devrc-rsw/. $T/; rm -f $T/.git
git -C ~/workspace/devrc-rsw show d86e5f81:scripts/resume-state.sh > $T/scripts/resume-state.sh
nix develop ~/workspace/devrc -c python3 -m pytest \
  $T/scripts/tests/test_resume_state_handoff_resolution.py -q -p no:cacheprovider --tb=no
#   expect 12 failed / 4 passed;  at HEAD the same selection is 16 passed

# 2. live end-to-end — needs a doc that exists in EXACTLY ONE linked worktree
bash ~/workspace/devrc-rsw/scripts/resume-state.sh \
  ~/workspace/devrc/claudedocs/handoff-linux-cpu-profiling.md | grep -E '^# repo:|^  handoff:'
#   expect the named doc, and `# repo:` pointing at the worktree that holds it

# 3. BOTH sandbox tiers, ONE AT A TIME (#1088) — read each runner's own RESULT: line
nix build ~/workspace/devrc-rsw#checks.x86_64-linux.pytests   --no-link --print-build-logs
nix build ~/workspace/devrc-rsw#checks.x86_64-linux.nodetests --no-link --print-build-logs

# 4. the mutation battery — grep NOT APPLIED before believing any `killed` count
PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c \
  python3 ~/workspace/devrc-rsw/scripts/tests/mutation_battery_resume_state.py
```
