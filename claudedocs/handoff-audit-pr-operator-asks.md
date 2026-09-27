# Handoff: audit-pr-operator-asks — 2026-09-27

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
`/audit-pr` round 0 step 1 asks for each requirement's "author of record: **Zach (quote the
ask)**" — but the auditor is dispatched read-only with a diff and had no way to read what the
operator asked for, so that branch was unreachable and his stated requirements landed on
`unattributed`, which the section treats as a finding. Give round 0 his own words.

- **closing-condition:** `check` — over round-0 reports recorded after `31033cdb`, on PRs
  whose commits carry a `Claude-Session-Id:` trailer, the ledger's `unattributed:` count is
  lower than the pre-ship baseline **and** no report raises a deletion candidate against a
  requirement the asks block quotes. Baseline measured 2026-09-26: **1,649 round-0 ledger
  lines across 728 sessions in 7 repos, most common line `requirements: 7 (unattributed: 2)`.**
  **Judgement over named evidence:** the operator reads that count.

## State now
- 🔴 **SHIPPED AND VERIFIED.** `#1887` squash-merged as **`31033cdb`** (2026-09-27T05:49:10Z),
  branch deleted. Confirmed **by CONTENT** — `scripts/lib/operator_asks.py`,
  `scripts/tests/mutation_battery_operator_asks.py`, the `EXPECTED_SKIPS` pin, `VIEWER_FIELD`
  and 4 `--include-answers` sites are all on `origin/main`. **Ancestry is FALSE and that is
  correct for a squash** — never read it as "did not land".
- **All four Tekton checks green on the merged head `1780318b`**, read by description not
  colour: `pytests` collected=24348 passed=24341 skipped=7 failed=0 · `nodetests` 1,720 ·
  `gotests` 461 · `cairn-client-runs`.
- 🔴 **The `nix build` SANDBOX tier was run locally — the gap every earlier commit here
  flagged and did not close** — with a proper control pair on the SAME tree:
  pre-fix `RESULT: FAIL (exit=1)`, fixed `RESULT: PASS (exit=0)`, both `SCOPE: FULL (30 of 30
  hermetic target(s))` and both `collected=24348 passed=24341 skipped=7 failed=0`. Identical
  measurements, opposite verdicts, sole delta the skip pin.
- **Deployed to BOTH hosts** via `scripts/ship.sh` (rc 0). Every per-host line read, not the
  verdict: workbench 624 artifacts resolve / **0 dangling** / 442 repo-sourced / **0 stale**;
  laptop fast-forwarded `6975b1b2 → 31033cdb`, 590 / 0 / 437 / 0. Cross-host agreement
  asserted — both at `31033cdb`. Neither host skipped. ⚠ `192.168.50.155` did not answer;
  ship fell back to the nebula address `10.42.0.100` on its own.
- ✅ **VERIFIED AGAINST THE SYMPTOM, not the rollout.** `readlink -f
  ~/.claude/skills/audit-pr/SKILL.md` → `/nix/store/qdkkx0jj…` — a NEW store path (was
  `pz6bbghd…`), so the switch genuinely swapped the `home.file` copy, and the deployed copy
  carries the new step-1 prose. Then a live `audit-dispatch.py 1887 --round 0` printed
  **11 asks / 16,023 B, 2 of them answers, 10 `<task-notification>` records dropped and
  named, 4 PR comments examined**, with the review-comment blind spot declared.
- ⚠ **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for
  this session. An unknown session id also answers 200 with an empty array, so this is a real
  reading and **not** a clean bill of health.

## What it does now, in one place
`audit-dispatch.py --round 0` prints `## THE OPERATOR'S OWN ASKS` **above** the round-0
section (step 1's input must precede the instruction that consumes it). Sources: his typed
messages **and his answers to questions the session asked him**, from
`Claude-Session-Id:` trailers in **this PR's own commits** (via `gh`'s `commits` field — no
git, no fetch), plus his own PR comments. Every unreadable source renders a named `UNKNOWN`
with `UNATTRIBUTED-UNKNOWN` guidance; `render()` has no path that emits a quiet empty block.

## Gotchas measured here
- 🔴 **`kind: "typed"` IS NOT THE OPERATOR, and the population that matters is the
  PRODUCER's output, not the corpus.** Over the rows `extract_user_msgs.records_of` EMITS —
  18,494 typed rows / 55,551,545 B — `<task-notification>` is 10,007 rows / 54,654,875 B
  (**98.39%**), a harness note 13 rows / 998 B, and **the operator 8,474 rows / 895,672 B
  (1.61%), median 17 B**. An earlier version measured RAW user-role transcript records
  instead (139,541,944 B, skill bodies 59.5%) and wrote eight classifier families for classes
  that **fire ZERO times** — the producer already removes them (`isMeta` drops injected skill
  bodies, `COMMAND_NAME` routes command tags, `clean_text` strips `<system-reminder>`,
  `BOILERPLATE_PREFIXES` already held two of the same patterns). **Ask what your producer
  hands you before measuring anything.**
- 🔴 **THE OPERATOR'S ANSWERS TO A QUESTION ARRIVE IN A `tool_result` BLOCK**, which
  `extract_from_content` ignores (`# ignore tool_result, image, tool_use, thinking`). MEASURED
  **1,491 records / 837,635 B across 594 sessions** — 93.5% again on top of the entire typed
  corpus. Recovered by an opt-in `--include-answers` / `kind=answer`, matched STRUCTURALLY
  (a `tool_result` whose `tool_use_id` names an assistant `tool_use` called
  `AskUserQuestion`) and never by text, because every Bash/Read result has the same shape.
  OFF by default so no shipped consumer's output moves.
- 🔴 **"THE OPERATOR" IS `viewerDidAuthor`, AND THAT PREDICATE WAS WRONG TWICE.** A nine-login
  bot DENYLIST filtered **0** of 115 comments across the 120 newest devrc PRs and missed
  `civitai-deploy` (130 of 177 comments, 74%, on the 60 newest `civitai/civitai` PRs). Its
  replacement, `authorAssociation in {OWNER,MEMBER,COLLABORATOR}`, is REPO MEMBERSHIP:
  measured, devrc has **11 collaborators, 10 of them other people**, all `MEMBER` — and
  `civitai-deploy` is `MEMBER` too, so the fix re-admitted the exact comment it replaced.
  **When a predicate's docstring answers a different question from the heading its result
  prints under, the heading is what readers believe.**
- 🔴 **A rc-0 RUN CAN BE A PARTIAL READ, AND THE PROOF IS ON STDERR.** The extractor exits
  **0** having read only some selected ids and says so with a `!` prefix
  (`print(f"! {note}", file=err)`): `! 1 of 2 selected session(s) have NO transcript on this
  host (peer host? pruned?)`. Discarding `err` made a half-resolved set render as complete
  with no UNKNOWN. ⚠ **One of its four `!` families is INFORMATION, not a gap** — the dedup
  note ("…**they are not empty**"): those messages WERE read. Reading it as a gap put a
  permanent UNKNOWN on complete reads, which is a permanently-red gate.
- 🔴 **A GUARD THAT ONLY THE GUARDED RUNNER CAN FIRE IS INVISIBLE TO A SUBSET RUN.** Landing
  a battery in `BATTERIES` adds a parametrized case that SKIPS, and an unpinned skip is a
  **GUARD 2** failure that reports `failed=0`. CI read `collected=24348 passed=24341
  skipped=7 failed=0` **FAILED** against main's `skipped=6` **SUCCESS** — the whole signal
  was 6→7. `run-tests.sh`'s third `EXPECTED_SKIPS` entry PREDICTS this in a comment ("If you
  land a battery with no multi-site row and CI goes red with `failed=0`, look here first")
  and the fourth records the note already paying off. **This was the third occurrence.**
  Every local run here was `python3 -m pytest <paths>`, which bypasses GUARD 2 entirely.
- 🔴 **A WRAPPER'S EXIT STATUS IS NOT THE BUILD'S.** A background `nix build … > log; echo
  NIXBUILD_RC=$?; grep …` was reported by the harness as **"exit code 0"** while
  `NIXBUILD_RC=1` and the runner's own line read `RESULT: FAIL (exit=1)`. The trailing `grep`
  owns the wrapper's status. Read the runner's verdict line, never the wrapper's.
- ⚠ **zsh has no word-splitting, and it bit a monitor written to avoid guessing.** A CI-poll
  loop did `printf '%s\n' $s | grep -c '=pending$'` over a joined string, counted the whole
  blob as ONE line, got 0, and printed `SETTLED` while two checks were still pending. Emit
  one row per line from `jq` and assert the expected check COUNT before believing a settle.
- ⚠ **Two of my own mutants scored as passes while testing nothing.** One was a SYNTAX ERROR
  (module failed to import, suite errored with no `FAILED` line → `WRONG-REASON` with an
  empty failure set); one globbed `*/` while its fixture sat three levels down, so it
  SURVIVED. Keep every mutation syntactically valid, and match the fixture's depth.
- ⚠ **An auditor's justification can be wrong while its finding is right.** Round 3's 🔴 (the
  red head) was real; its stated reason — that the private glob could resolve a `subagents/`
  transcript — was **refuted by measurement**: excluded transcripts live at
  `<project>/<session-id>/subagents/<id>.jsonl`, three levels down, and the pattern was `*/`,
  one level. 965 matches on this host, **0** in an excluded directory. It was nearly shipped
  as a docstring.

## Open investigations — live diagnosis state

### `viewerDidAuthor` cannot separate "the operator typed it" from "an agent posted it with his token"
- as-of: 2026-09-27
- **Symptom + exact repro:** run `audit-dispatch.py <pr> --round 0` on a PR whose comments
  were posted by an agent via `gh pr comment` (every `/audit-pr` claims block is). They are
  inlined under `## THE OPERATOR'S OWN ASKS` and counted as his.
- **Observed (with values):** on `#1887` itself, `PR comment: 4 comment(s) examined, 4 from
  the operator` — all four are agent-written claims blocks that I posted through `gh`, which
  authenticates as `ZacxDev`, so `viewerDidAuthor: true` is literally correct and the
  attribution is wrong. Blast radius is bounded: the block labels the source
  `### from the PR comment` and prints the author login as the group header.
- **Ruled out:** ~~`authorAssociation` would do better~~ — it is repo membership, admits
  devrc's 10 other collaborators and `civitai-deploy`; measured. `via: measurement`
- **Ruled out:** ~~a login comparison against the PR author~~ — same failure, an agent posts
  as the operator either way. `via: code`
- **Leading hypothesis:** no field on a `gh` comment row distinguishes them, because the
  distinction is not in GitHub's data model — the agent IS acting as the operator. The
  discriminator would have to be local (a marker the posting tool writes, e.g. the
  `🤖 Generated with` footer `/audit-pr`'s own comments already carry).
- **Next probe:** measure how often it matters before building anything —
  `gh pr list --repo innovation-upstream/devrc --state merged --limit 60 --json number` then,
  per PR, `gh pr view <n> --json comments --jq '[.comments[]|select(.viewerDidAuthor)|
  {n:(.body|length), bot:(.body|test("Generated with \\[Claude Code\\]"))}]'` — the share of
  operator-attributed comment BYTES carrying that footer is the size of the problem.

## Next steps (ranked)
1. **Read the closing-condition count.** Over round-0 reports recorded after `31033cdb` on
   PRs whose commits carry a session trailer, compare `unattributed:` against the 2026-09-26
   baseline (1,649 ledger lines / 728 sessions / most common `requirements: 7 (unattributed:
   2)`), and check no report raises a deletion candidate against a requirement the asks block
   quotes. 🔴 Do not re-tune the feature off n<10.
   forcing: none
2. **Decide the agent-posted-comment question** (open block above) — run its Next probe
   FIRST; if the share is small, record "won't fix" rather than building a marker check.
   Repo `devrc`, `scripts/lib/operator_asks.py`.
   forcing: none
3. **Consider whether `extract_user_msgs.py --include-answers` should become the default.**
   It is off so no shipped consumer moved, but the `find-session --arc` footer arguably wants
   the operator's answers too. Blocked on `handoff-arc-user-messages.md` NEXT #2 — that arc
   is mid-measurement against the current contract and flipping the default would invalidate
   its in-flight reach count.
   forcing: none

## Defects (batched)
- ⚠ **The `#1887` squash subject on `main` says "round 0 reads the operator's own asks"** and
  the PR went on to four audit rounds that rewrote most of it. Not editable without rewriting
  a shared `main`, so it stands; the commit BODY and four PR comments carry the corrections.
- ⚠ **`handoff-audit-pr-ladder.md` is 194,314 B** — far over the enforced 65,536 B per-doc
  cap, so it sits in the grandfather ledger. It is a CLOSED arc (condition met 2026-09-15);
  a prune pass on it is available work, unrelated to this arc.

## How to verify
```bash
# 1. the asks block renders on a real PR, from the DEPLOYED skill
python3 $DEVRC/scripts/audit-dispatch.py 1887 --repo innovation-upstream/devrc --round 0 \
  | awk '/THE OPERATOR.S OWN ASKS/,/^\*\*Ledger/'     # expect asks + a Sources read block
# 2. the answers half — the flag is what recovers 93.5% more of his words
python3 $DEVRC/scripts/session-analysis/extract_user_msgs.py \
  --session <a-session-that-was-asked-a-question> --include-answers --jsonl \
  | python3 -c 'import sys,json,collections; print(collections.Counter(json.loads(l)["kind"] for l in sys.stdin if l.strip()))'
# expect {'typed': N, 'answer': M} with M>0; WITHOUT the flag, no `answer` rows at all
# 3. the guards — and the battery, whose P1 positive control must be KILLED
nix develop $DEVRC -c python3 -m pytest $DEVRC/scripts/tests/test_operator_asks.py -q
nix develop $DEVRC -c python3 $DEVRC/scripts/tests/mutation_battery_operator_asks.py
# expect 26 KILLED · 0 SURVIVED · 0 SKIPPED, C0 GREEN, restored by digest: True
# 4. 🔴 the tier the merge gates on — a SUBSET run cannot see GUARD 2
nix build $DEVRC#checks.x86_64-linux.pytests --no-link -L   # read RESULT: and SCOPE:, not the wrapper's rc
# 5. both hosts carry it
bash $DEVRC/scripts/drift-check.sh   # read every per-host line, not the verdict
```
