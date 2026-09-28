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

- **closing-condition:** `check` — `python3 $DEVRC/scripts/round0-attribution-rate.py` reports
  the post-cut **in-population RATE** (mean `unattributed` per round-0 report, and
  `unattributed` as a share of `requirements`) as LOWER than the pre-cut rate, at
  n ≥ `MIN_SESSIONS` (10 **distinct sessions**, not ledger lines) **and** no report raises a
  deletion candidate against a requirement the asks block quotes.
  **Judgement over named evidence:** the operator reads that count.
  - 🔴 **IN-POPULATION MEANS "THE OPERATOR'S WORDS ACTUALLY REACHED THE AUDITOR"** — an ask
    RENDERED from a session transcript (`### from the session transcript` in the brief). It
    does **NOT** mean "the PR's commits carry a `Claude-Session-Id:` trailer", which is the
    definition this condition inherited from `#1887` and is the **wrong fact**: `render()`
    prints `N session(s) NAMED BY … trailers` whenever trailers NAMED a session — including
    when nothing could be read off them — so the inherited reading counted reports whose own
    block says *NO OPERATOR ASK COULD BE READ FOR THIS PR*. Found by **`#1901 round 0`**,
    reproduced, and fixed; see Defects.
  - 🔴 **The left-hand side is a RATE, not a count** — see Defects for why the original
    absolute count could never be met.
  - **Read the CONTEMPORANEOUS control beside it.** The run prints POST-cut
    out-of-population — same skill revisions, models and repos, differing only in whether the
    asks arrived — because the comparator is otherwise a SELECTED post population against an
    UNSELECTED pre one. A difference that also appears in the control is not the asks block.
  - **Reproducible pre-cut baseline, method named:** assistant-authored text blocks ONLY, one
    round-0 ledger line = one report, corpus `~/.claude/projects` on **this host**, cut =
    `31033cdb`'s **committer date** (`%cI` — when the squash LANDED on `main`, not when its
    author wrote it; the run prints which clock it read). Measured 2026-09-28: **493 ledger
    lines in 236 REAL SESSIONS (474 transcript files) across 7 projects, mean 3.365
    unattributed per report, share 0.331.** Re-derivable by re-running the command — that is
    the point of it being a command.
    - ⚠ **`474` was published here as a session count and it is a FILE count** — wrong by
      ~2× as a number of observations. A round-0 report is written by an auditor SUBAGENT
      whose transcript is `<project>/<sid>/subagents/agent-*.jsonl`: a different file from the
      parent's, and several auditors of one session are several files again (6,692 files on
      this host against 1,563 distinct session ids). `#1901 round 1` found it; the run now
      prints `sess` and `files` side by side, and the FLOOR counts sessions.
    - ⚠ **The clock is load-bearing and the coincidence here is not a property of the tool.**
      `31033cdb`'s author and committer dates are identical to the second, so this baseline
      reads the same either way — which is why reading the wrong one was invisible. Over the
      1,200 newest `main` commits, 10 diverge, the widest by **42.6 min** (`daa6fd65`:
      author 13:20:35, committer 14:03:11). The author date would put reports written in
      that window in the POST bucket while the fix was not yet on `main` or deployed.
  - ⚠ **The doc's earlier `1,649 / 728 / 7` figure is NOT reproducible by this method and is
    SUPERSEDED as a comparator.** Two defensible methods over the same corpus disagree and
    neither yields it: assistant-authored blocks give the 493 above; counting every record of
    any role gives **1,977 / 707 / 8**. Kept here so nobody re-derives it as if it were the
    baseline. A number quoted without its method has no defined left-hand side.
  - **Today's real answer is `VERDICT: NOT MEASURABLE (n=1 distinct session(s) / 2 transcript
    file(s) / 3 report(s); floor 10)`** — the tool refuses to compare below ten sessions on
    purpose, so it cannot be used to justify re-tuning the feature. Fresh run, 2026-09-28T05:0xZ,
    every figure from that one run:
    | bucket | reports | sess | files | mean/report | share |
    |---|---|---|---|---|---|
    | PRE-cut (all) | 493 | 236 | 474 | 3.365 | 0.331 |
    | PRE-cut in-population | 2 | 1 | 1 | 5.500 | 0.423 |
    | POST-cut (all) | 22 | 15 | 21 | 4.318 | 0.289 |
    | POST-cut out-of-population **[control]** | 18 | 14 | 18 | 3.278 | 0.241 |
    | POST-cut in-population | 3 | 1 | 2 | 4.667 | 0.389 |
    Post-cut reasons: 16 × the session source was consulted and could not be read · 3 × an
    ask arrived · 2 × no asks block at all · 1 UNKNOWN. (These GROW with every audit —
    defect 2 in miniature — so re-run rather than quoting this table.)
  - ⚠ **PRE-cut in-population is NOT structurally zero, and a label here said it was.** The
    feature's own development session rendered real asks blocks from its BRANCH before the
    squash landed: 2 reports / 1 session. The run's note now describes whichever case holds;
    it is not the comparator either way.
  - The number the condition reads is the **in-population distinct-SESSION count**, never the
    bucket's size and never a file count — measured on `#1901`'s own round 0, two ledger lines
    came from ONE session, so a report-counting floor let one verbose audit supply 20% of it.

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
1. **Re-run the closing-condition RATE** — `python3 $DEVRC/scripts/round0-attribution-rate.py`
   (not a hand count; the 2026-09-26 `1,649` figure is superseded, see closing-condition). It
   refuses below n=10 and exits 6 today. Then read the judgement half yourself: no report may
   raise a deletion candidate against a requirement the asks block quotes. 🔴 Do not re-tune
   the feature off n<10.
   forcing: none
2. **Decide the agent-posted-comment question** (open block above) — run its Next probe
   FIRST; if the share is small, record "won't fix" rather than building a marker check.
   Repo `devrc`, `scripts/lib/operator_asks.py`.
   forcing: none
3. **Consolidate the corpus walk with `audit-rule-firing-sweep.py`** — HYGIENE, not a live
   bug, and measured as such by `#1901 round 0`: the two walks' only behavioural divergence is
   that the sweep credits an `Agent`/`Task` `tool_result` as signal, and that fires **0 times**
   (0 ledger-line occurrences in any such block), while the injected/assistant split sums to
   the rate tool's own total exactly. So the risk of leaving them separate is duplication, not
   disagreement. Both keep their own walk on purpose (the shared `iter_transcripts` excludes
   `subagents/`, where auditor transcripts live) and both carry a `JSONL_GLOB_SITES` row.
   forcing: none
4. **Consider whether `extract_user_msgs.py --include-answers` should become the default.**
   It is off so no shipped consumer moved, but the `find-session --arc` footer arguably wants
   the operator's answers too. Blocked on `handoff-arc-user-messages.md` NEXT #2 — that arc
   is mid-measurement against the current contract and flipping the default would invalidate
   its in-flight reach count.
   forcing: none

## Defects (batched)
- 🔴 **THE CLASSIFIER WAS BLIND TO THE DOMINANT DELIVERY PATH — a brief the auditor READS.**
  `audit-dispatch.py`'s output is redirected to a scratchpad `.md` and the dispatch prompt says
  to read it, so the block arrives as a `cat -n` tool_result with every line prefixed
  `<spaces><n>\t`. A `^`-anchored matcher (added in round 0 to stop a source READ of
  `operator_asks.py` counting as a received block) cannot match that. MEASURED by **`#1901
  round 1`** (blind) and reproduced: **13 of 20** post-cut reports were scored `out` with the
  reason *"no asks block in this session (a pre-fix report)"* while their numbered copy
  demonstrably carried `**Sources read:**`. Three consequences, all of them the instrument
  lying in the reader's favour: the printed reason was affirmatively FALSE; the contemporaneous
  control row was contaminated with sessions that DID receive a block; and `post_in` was
  deflated with the floor unreachable by the path that actually delivers. Fixed by an OPTIONAL
  line-number prefix, applied to **every** line-anchored matcher (a fix to one while a sibling
  stayed blind would move the defect, not remove it), with both poles pinned — a numbered real
  render matches, a numbered source read still does not. After the fix the same corpus reads
  **2** "no asks block" instead of 14, and 16 correctly as "consulted and could not be read".
  ⚠ Two claims of the round-0 fix were REFUTED in the process and are retracted rather than
  patched: that "every one of the other 39 [substring matches] is a source read or a discussion
  of this feature" (round 1 found ≥11 real renders in that residual), and that the PRE-cut
  in-population row "cannot be non-zero". A guard-comment that reads as coverage is what stops
  the next reader looking. Also fixed in the same round: an undefined `unattributed` share was
  read as 0 and printed a **fabricated** delta inside the verdict line
  (`share — vs 0.750, Δ-0.750`); the `ANCHORS` header claimed two reaches the pin did not have
  (both measured INERT — rc 4, not rc 5 — now implemented); and the exit-code legend described
  an unreadable corpus as rc 2 where the code refuses with rc 4.
- 🔴 **THE INHERITED POPULATION DEFINITION BOUGHT THE WRONG FACT, and it was measurably
  unsafe.** `#1887`'s closing condition said "PRs whose commits carry a `Claude-Session-Id:`
  trailer", and `round0-attribution-rate.py` implemented that literally by reading
  `render()`'s `N session(s) NAMED BY … trailers` line as evidence the asks had been read.
  That line is guarded by `if session_ids:` alone and is emitted BESIDE
  `! session transcript: UNKNOWN — …` in the common case where the trailers name a session
  whose transcript is not on this host — which `audit-dispatch.py` records as the ORDINARY
  case, because the operator runs two hosts. So a report whose own block says *NO OPERATOR ASK
  COULD BE READ FOR THIS PR* scored IN-population and entered the closing condition's
  left-hand side; on the live corpus 3 of 20 post-cut reports were in exactly that state.
  Found by **`#1901 round 0`** (blind), reproduced by the coordinator. Fixed structurally
  rather than by flipping a precedence: *named by a trailer* is now the `selected` role
  (SELECTION), `answered` comes only from a rendered `### from the session transcript`
  heading, `IN_POPULATION_ROLES` is a one-line ledger, and **both poles of a live `render()`
  are classified on every run** (exit 5) because every anchor string was already correct when
  the defect shipped. The population is now *the operator's words actually reached the
  auditor*. ⚠ The script's own blind-spot 8 had asserted the SAFE direction ("trailers exist
  but the transcript was pruned is out-of-population") while the code took the unsafe one —
  that sentence was unreachable, and a doc naming the safe direction over unsafe code is worse
  than silence.
- 🔴 **The original closing condition was an ABSOLUTE CORPUS-WIDE COUNT, so it could only
  GROW.** It compared a post-ship `unattributed:` count against a pre-ship count over the same
  cumulative corpus: every pre-fix report stays in that corpus forever and each new one adds to
  it, so the left-hand side increases monotonically and "lower than the baseline" was
  unfalsifiable in the direction it wanted. Measured while fixing it: the whole-corpus count
  moved 493 → 509 in one day with no change to the feature. Closed by making the statistic a
  RATE over each bucket's own reports — `scripts/round0-attribution-rate.py`.
- 🔴 **And its baseline was not reproducible** — the `1,649 / 728 / 7` figure cannot be
  re-derived by either of the two defensible methods over the same corpus (493/474/7
  assistant-authored, 1,977/707/8 all-roles). The instrument now STATES its method in its own
  output, so the next reader compares like with like.
- ⚠ **The `#1887` squash subject on `main` says "round 0 reads the operator's own asks"** and
  the PR went on to four audit rounds that rewrote most of it. Not editable without rewriting
  a shared `main`, so it stands; the commit BODY and four PR comments carry the corrections.
- ⚠ **`handoff-audit-pr-ladder.md` is 194,314 B** — far over the enforced 65,536 B per-doc
  cap, so it sits in the grandfather ledger. It is a CLOSED arc (condition met 2026-09-15);
  a prune pass on it is available work, unrelated to this arc.

## How to verify
```bash
# 0. THE CLOSING CONDITION ITSELF — the rate, both buckets, and the refusal
python3 $DEVRC/scripts/round0-attribution-rate.py        # exit 6 = NOT MEASURABLE (n<10)
# reads ~/.claude/projects on THIS host only; no `gh`, no network. Expect today:
# PRE-cut 493 reports / 236 sessions / 474 files / mean 3.365 · POST-cut in-population
# 1 session · exit 6.  `sess` is the observation count; `files` is not (subagents).
# Read the DISPOSITIONS reasons block: every post-cut report says why it landed where it did.
# 🔴 Two reasons that must stay RARE, and both were bugs when they were common:
#    "no asks block in this session (a pre-fix report)" over a block delivered as a Read, and
#    `NAMED BY` (selection) counted as the operator's words having arrived.
# Its guards (incl. the two-way pin against scripts/lib/operator_asks.py):
nix develop $DEVRC -c python3 -m pytest \
  $DEVRC/scripts/tests/test_round0_attribution_rate.py -q
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
