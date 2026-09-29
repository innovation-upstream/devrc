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
    - ⚠ **`474` was published here as a session count and it is a FILE count** — **2.01×**
      the number of observations (474 files / 236 sessions). A round-0 report is written by an
      auditor SUBAGENT whose transcript is `<project>/<sid>/subagents/agent-*.jsonl`: a
      different file from the parent's, and several auditors of one session are several files
      again. `#1901 round 1` found it; the run now prints `sess` and `files` side by side plus
      the per-bucket ratio, and the FLOOR counts sessions.
    - 🔴 **TWO DIFFERENT RATIOS — do not quote one for the other.** The **2.01×** above is the
      PRE-BUCKET figure (files that carry a report). The **CORPUS** figure is **~6.9×**,
      measured with the script's own `_session_id_of` at **2026-09-28T17:05Z**: **6,739 files
      / 973 sessions**, 5,766 under `subagents/`. ⚠ A `1,563 distinct session ids` figure
      circulated in this arc and is **WITHDRAWN**: it came from taking the first path segment
      *without stripping `.jsonl`*, which counts a session holding both a top-level transcript
      and a `subagents/` dir twice — measured at the same instant, naive **1,570** against 973.
      ⚠ **An earlier version of this bullet said "session ids cannot fall while files rise" as
      the reason it was caught. That invariant is FALSIFIED** — a pruned transcript breaks it,
      and four runs over three days gave 6,717/973, 6,736/973, 6,739/973 with sessions flat
      while files rose. The sound claim is the narrower one: **the same method cannot give both
      1,563 and 973.** 🔴 Every corpus figure here drifts and is stamped for that reason —
      treat it like the tables marked "re-run, never quote". The MECHANISM does not drift, and
      `test_the_naive_session_derivation_double_counts_and_ours_does_not` pins it so a wrong
      number fails rather than reads fine. **Third irreproducible figure in this arc — derive
      it with the code, or do not write it.**
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
    purpose, so it cannot be used to justify re-tuning the feature. Fresh run, 2026-09-28T06:0xZ,
    every figure from that ONE run (files/sess: PRE 2.01×, POST 1.42×):
    | bucket | reports | sess | files | mean/report | share |
    |---|---|---|---|---|---|
    | PRE-cut (all) | 493 | 236 | 474 | 3.365 | 0.331 |
    | PRE-cut in-population | 2 | 1 | 1 | 5.500 | 0.423 |
    | POST-cut (all) | 28 | 19 | 27 | 3.857 | 0.268 |
    | POST-cut out-of-population **[control]** | 23 | 18 | 23 | 3.043 | 0.232 |
    | POST-cut in-population | 3 | 1 | 2 | 4.667 | 0.389 |
    Post-cut reasons: 21 × the session source was consulted and could not be read · 3 × an
    ask arrived · 2 × UNKNOWN · 2 × no asks block at all. 🔴 **These GROW with every audit** —
    the post bucket went 20 → 22 → 28 across three runs of this one arc, which is defect 2 in
    miniature: **re-run, never quote this table.**
  - ⚠ **PRE-cut in-population is NOT structurally zero, and a label here said it was.** The
    feature's own development session rendered real asks blocks from its BRANCH before the
    squash landed: 2 reports / 1 session. The run's note now describes whichever case holds;
    it is not the comparator either way.
  - The number the condition reads is the **in-population distinct-SESSION count**, never the
    bucket's size and never a file count — measured on `#1901`'s own round 0, two ledger lines
    came from ONE session, so a report-counting floor let one verbose audit supply 20% of it.

## State now
- 🔴 **SHIPPED. `#1901` squash-merged as `79a9b22a`** (2026-09-29T06:11:31Z), branch deleted.
  Confirmed **by CONTENT** — `scripts/round0-attribution-rate.py`,
  `scripts/tests/test_round0_attribution_rate.py` and
  `scripts/tests/mutants-round0-attribution-rate.py` are all on `origin/main`.
  **Ancestry is FALSE after a squash and that is correct** — never read it as "did not land".
- **The instrument answers the closing condition now.** A live run prints a per-report RATE,
  a floor counting **distinct sessions**, and today's honest verdict:
  `VERDICT: NOT MEASURABLE (n=1 distinct session(s) / 2 transcript file(s) / 3 report(s); floor 10)`.
  The refusal is IN THE TOOL, not in prose, so it cannot be argued past.
- **The ladder ran FIVE blind rounds and is CLOSED.** Round 4 and round 5 both returned **no 🔴**,
  and every structural claim was re-derived by the auditor under its own mutations rather than
  accepted from the author. Claims blocks for rounds 1–4 are posted on the PR.
- 🔴 **Gated on the MERGED TREE, not the branch.** `strict: false` means a green check is a claim
  about the PR branch, and `main` moved `a7d7d32f → 3573a413` while this was open. So
  `origin/main + 37be792f → 6465ce67` was built and gated directly: battery `rc 0 · 39 of 39
  killed · all on their own guard's ASSERTION`, and `pytests` `SCOPE: FULL (30 of 30 hermetic
  target(s))`. Its `failed=5` was **entirely inherited** — a pristine `origin/main` worktree with
  no merge reproduced all five (`browser SKILL.md` 12,981 B against a 12,038 B budget, from
  `0786a55e`). ⚠ **That breach is now FIXED by someone else's `#1917`** (12,020 B); re-measured
  2026-09-29T06:4xZ, those 160 tests pass and `main-green-check.service` is back to
  `ExecMainStatus=0`. **Do not carry "main is red" forward — it was true for ~40 minutes.**
- 🔴 **`#1914` (the public-IP gate carve-out) is CLOSED UNMERGED — operator's call, on evidence.**
  Branch `fix/ip-gate-slice-false-positive` retained. A lexical carve-out on a security gate
  produced a NEW false negative in **three consecutive rounds**; full table in the PR's closing
  comment. The `parts[1::2]` false positive therefore STANDS: spell the slice differently, as
  `round0-attribution-rate.py` does (`range(1, len(parts) - 1, 2)`).
- 🔴 **CARRIED FORWARD, and do not conflate the two: the FEATURE is deployed, the INSTRUMENT is
  not.** `#1887` (`31033cdb`, the asks block itself) shipped to **BOTH hosts** via `scripts/ship.sh`
  rc 0 on 2026-09-27 — workbench 624 artifacts / 0 dangling / 0 stale, laptop fast-forwarded
  `6975b1b2 → 31033cdb`, cross-host agreement asserted, neither host skipped — and was verified
  against the symptom, not the rollout (`readlink -f ~/.claude/skills/audit-pr/SKILL.md` moved to a
  NEW store path). 🔴 **`#1901` has had NO `ship.sh` run.** `scripts/round0-attribution-rate.py` is
  on `origin/main` and nothing deploys it (`git grep round0-attribution -- nix/` is empty), so it is
  invoked by absolute path and needs no switch — but any future change to a `home.file`-managed path
  in this arc does. **Merged ≠ deployed.**
- **No `clawgate-task:` field.** `clawgate_handoff.sh resolve` exited **5** — 0 tasks for this
  session. An unknown session id also answers 200 with an empty array, so this is a real reading
  and **not** a clean bill of health.

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

- 🔴 **EVERY DEFECT THIS LADDER FOUND AFTER ROUND 0 WAS IN A GUARD'S SELF-DESCRIPTION, NEVER ITS
  ARITHMETIC — five rounds, and the count is the finding.** Comments and docstrings claiming
  coverage the implementation did not have, **twice inside guards added to close an earlier
  finding**, plus **five separate claims of ABSENCE** that were each a claim about a path nobody
  had measured (`every one of the other 39 is a source read`; `[struct. 0]` on a row the next run
  printed 2 in; `measured impact today is ZERO`; `the first battery with a real multi-site row`
  — actually the fourth; `both unhandled cases resolve toward REPORTING` — measured, both
  exempt). **The correct outcome for an unsupported claim is a sentence saying it has none.**
  A fresh rationale composed under pressure to supply one is how the next round's finding gets
  written.
- 🔴 **A MUTATION SWEEP IN A SCRATCHPAD IS NOT AN INSTRUMENT. Commit it.** The moment
  `mutants-round0-attribution-rate.py` landed in the tree its own coverage check (`C4`) reported
  **7 of 9 anchors and a whole branch named by NO mutant** — invisible while the list was
  hand-maintained in a scratch file, because a hand-maintained list cannot see what it omits.
  Landing it also **un-skipped a ledger control that had never once been observed working**
  (`test_the_PAIR_check_goes_RED_on_a_real_battery_COPY`; this is the 4th battery with a
  multi-site row, not the first). ⚠ And it must gate, not report: `if gaps: rc = 1`.
- 🔴 **A KILLED MUTANT PROVES NOTHING UNTIL YOU READ *HOW* IT DIED.** One died on a `KeyError`
  raised by the **test's own subscript** — scored KILLED while testing nothing. The sweep now
  derives `KILLED(assert)` vs `KILLED(error)` from the failure text and calls an `error` kill
  **UNPROVEN** — with the caveat, in its own docstring, that the label is still text and the
  **named-killer set** is the primary signal. Related and distinct: a `WRONG-KILLER` has exactly
  **two** causes — a dead expectation, or a real guard gap — and **which one holds must be
  established BEFORE the expectation is touched.** One row had `own=[] quality=none`: 19 tests
  failed and not one was its named killer, and it was a genuine **isolation-seam** gap (the named
  test asserted the primitive; the mutation moved the CALL SITE behind a filter). Fixed by
  widening the GUARD, not the expectation.
- 🔴 **THREE MUTANTS SURVIVED A GREEN RUN BEFORE THEIR FIXTURE EXISTED, in one PR** (`#1914`) —
  each because every existing fixture was rejected by an EARLIER condition, so the mutated one
  never executed. The control is mechanical: feed a value the earlier conditions CANNOT reject,
  and make sure different mutants die to DIFFERENT fixtures. A mutant killed by exactly one
  fixture is the strongest evidence that condition does work.
- 🔴 **A HAND-RUN MEASUREMENT IS A CLAIM, AND TWO OF MINE WERE WRONG.** (a) I reported
  **1,563 distinct session ids**; the real figure by the script's own `_session_id_of` is **973**
  — my probe took the first path segment WITHOUT stripping `.jsonl`, so every session with both
  a top-level transcript and a `subagents/` dir counted twice (973 + 597 nested = 1,570, overlap
  597). It reached the doc, the script, a test and a PR body before a blind round caught it.
  (b) I recommended the whitespace-skip fix for the IP gate; it would have exempted
  `ssh [<addr>]` and `curl [<addr>]:443`, because a bare command word ends in an identifier
  character. **The test I had insisted be driven on realistic values is what refused it.**
  🔴 And the reasoning that caught (a) — *"session ids cannot FALL while files RISE"* — is itself
  **falsified**: measured 6,739→6,765 files with sessions 973→966. A pruned transcript breaks it.
  The narrow survivor is only *the same method cannot give both 1,563 and 973*.
- 🔴 **A CORPUS FIGURE IS A MEASUREMENT, NOT A CONSTANT — stop publishing bare ones.** Three
  figures in this arc were irreproducible within a day. What is durable is the **mechanism**: the
  committed test pins that the naive derivation double-counts and the real one does not, which
  survives the census drifting. Same for wall-times: two runs of one tree minutes apart gave
  36.5 s and 74.2 s (and 91 s in a third), so the stale "6-minute"/"40-minute" sweep constants
  were **withdrawn rather than replaced**, and a "~27×" ratio that reached a PR-body heading was
  retracted (the same ratio measures 25×–194× depending where the caller stands).
- 🔴 **`cwd` DECIDES PYTEST'S ROOTDIR, AND IT COST ~90×.** A battery passing an absolute path but
  no `cwd` made rootdir the common ancestor of the caller's cwd and the arg: **0.37 s with
  `cwd=tree` vs 65.44 s without**, × 39 invocations. One word took the sweep from ~45 min to
  ~30–90 s — the difference between an instrument people re-run and one they quote. ⚠ The
  internal cause was NOT isolated (`--noconftest` was still slow) and deliberately carries no
  explanation.
- 🔴 **`cairn-client-runs` CANNOT OBSERVE A devrc CHANGE — stronger than "it was cached".** Its
  derivation copies **no devrc source** (only the `cairn` package plus an inline fixture), so it
  is bit-identical across every devrc commit. It also prints no `RESULT:` line on success
  (`flake.nix` echoes only on the failure arms, then `touch "$out"`), so vouch for it on exit
  status + a realised output path and SAY that is what you did. Accepting or rejecting its silent
  pass carries zero information about your PR.
- ⚠ **A LEXICAL CARVE-OUT ON A SECURITY GATE COULD NOT BE MADE SAFE — the dead end, recorded so
  it is not re-attempted blind.** `#1914` (closed unmerged) leaked a false negative three rounds
  running: an adjacency condition missed `grid[1::2, ::3]`; quote members of `SUBSCRIPTABLE_CHARS`
  exempted `bind: "[<addr>]:53"` (canonical YAML/JSON/Go/shell IPv6 endpoint form); and quote
  **parity** was then defeated by an apostrophe in ordinary prose (`# don't forget the peer
  '[<addr>]:53'`) — reachable in committed prose, since `claudedocs/**` is deliberately not in
  `SKIP_DIRS`. Each fix was correct about its own shape and opened another. **The asymmetry is
  the whole argument: a false positive costs one line once, a false negative publishes an
  address.** The deterministic alternative (`tokenize`/`ast` for `.py`, strict elsewhere) is
  named and NOT done — it drops the module's line-at-a-time property, helps only `.py`, and puts
  a parser inside a security gate. Pick it only if the false positive becomes more than an
  annoyance.
- ⚠ **An agent that stops mid-wait on a long build re-notifies with no new information.** Two did.
  Collection is mechanical: `TaskStop` it and run the build yourself, logs to separate FILES
  (never a pipe — a piped `nix build` log produced a 0-byte "success" on this box), verdict
  recovered from `nix path-info --derivation` → `nix log` with `$DRV` guarded for emptiness, and
  `wc -c` before reading. Also: **six accumulated background waiters evicted a running sweep**,
  and `pgrep -f mutants` matched **another session's battery in a different repo** — resolve PIDs
  and re-verify `/proc/<pid>/cwd` at the moment of the kill.
- ⚠ **zsh ate a git ref.** `$ref:scripts/...` → `bad substitution`: `:s` is a history modifier.
  Brace it (`${ref}:path`). The failure is loud here; the dangerous version is silent.

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
1. **Re-run the closing-condition RATE and read the judgement half.**
   `python3 $DEVRC/scripts/round0-attribution-rate.py` — it refuses below n=10 (distinct
   SESSIONS, not reports) and exits 6 today. Then check the half no tool can check: no report
   may raise a deletion candidate against a requirement the asks block quotes. 🔴 Do not
   re-tune the feature off n<10, and do not hand-count — the `1,649` figure is superseded and
   every corpus number in this doc drifts.
   forcing: none
2. **Decide the agent-posted-comment question** (`## Open investigations` above) — run its Next
   probe FIRST; if the share is small, record "won't fix" rather than building a marker check.
   Repo `devrc`, `scripts/lib/operator_asks.py`.
   forcing: none
3. **Consolidate the corpus walk with `audit-rule-firing-sweep.py`** — HYGIENE, not a live bug,
   measured as such by `#1901 round 0`: the only behavioural divergence is that the sweep credits
   an `Agent`/`Task` `tool_result` as signal, and that fires **0 times**. Both keep their own walk
   on purpose (shared `iter_transcripts` excludes `subagents/`, where auditor transcripts live).
   forcing: none
4. **Consider whether `extract_user_msgs.py --include-answers` should become the default.**
   Blocked on `handoff-arc-user-messages.md` NEXT #2 — that arc is mid-measurement against the
   current contract and flipping the default would invalidate its in-flight reach count.
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
  render matches, a numbered source read still does not. After the fix the corpus reads
  **2** "no asks block" and **16** "consulted and could not be read" — both stamped
  2026-09-28T04:57Z, and both drifting (at 17:0xZ the same corpus read 2 and 22). 🔴 Re-run;
  this paragraph is about not quoting drifting counts and must not become an instance of it.
  ⚠ **An "instead of 14" comparison appeared here and is WITHDRAWN as unverifiable.** The
  pre-fix figure round 1 reported is **13 of 20**; a later sentence said the same corpus read
  "2 instead of 14", and both cannot be one corpus's pre-fix count — the post bucket itself grew
  20 → 22 between the two measurements. Re-deriving it needs the pre-fix code against a corpus
  that has since changed, so neither number is recoverable now. **13 of 20** is what round 1
  measured and is the only figure kept; the delta is stated as the post-fix counts, not as a
  difference. Picking whichever read better is exactly how this arc's irreproducible numbers got
  in.
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
  heading, `IN_POPULATION_ROLES` is **DERIVED** from the `BRANCHES` precedence ledger rather
  than being a second source of truth (`round0-attribution-rate.py:591-592` —
  `tuple(r for r, (disp, _why) in BRANCHES.items() if disp == "in")`), and **EVERY pole in
  `BEHAVIOURAL_POLES` is classified from a live `render()` on every run** (exit 5) because
  every anchor string was already correct when the defect shipped. 🔴 **Read
  `BEHAVIOURAL_POLES`, never a count here** — the script's own rule at `:143-145`, and this
  sentence said "both poles" and "a one-line ledger" after the set had grown past two and the
  ledger had stopped being a literal, which is the same count-in-prose defect it records.
  (`f8506db7`: five poles, one per `BRANCHES` branch.) The population is now *the operator's
  words actually reached the auditor*.
  ⚠ The script's own blind-spot 8 had asserted the SAFE direction ("trailers exist
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
  re-derived by either of the two defensible methods over the same corpus (493 reports in
  **236 sessions** / 474 FILES / 7 projects assistant-authored — note the middle number of the
  original triple was a FILE count, the arc's headline error; 1,977/707/8 all-roles). The instrument now STATES its method in its own
  output, so the next reader compares like with like.
- ⚠ **The `#1887` squash subject on `main` says "round 0 reads the operator's own asks"** and
  the PR went on to four audit rounds that rewrote most of it. Not editable without rewriting
  a shared `main`, so it stands; the commit BODY and four PR comments carry the corrections.
- ⚠ **`handoff-audit-pr-ladder.md` is 194,314 B** — far over the enforced 65,536 B per-doc
  cap, so it sits in the grandfather ledger. It is a CLOSED arc (condition met 2026-09-15);
  a prune pass on it is available work, unrelated to this arc.

## How to verify
```bash
# 1. the shipped instrument answers the closing condition (exit 6 = NOT MEASURABLE today)
python3 $DEVRC/scripts/round0-attribution-rate.py; echo "rc=$?"
# expect a RATES table (PRE/POST, in-population, and the out-of-population CONTROL row),
# then VERDICT: NOT MEASURABLE (n=<N> distinct session(s) …; floor 10). rc 6. Numbers DRIFT.

# 2. the committed battery — the sweep is evidence only because it is re-runnable
PYTHONDONTWRITEBYTECODE=1 nix develop $DEVRC -c \
  python3 $DEVRC/scripts/tests/mutants-round0-attribution-rate.py; echo "rc=$?"
# expect rc 0 · C4 "no mutant at their DECLARING site: none" · C0 control green ·
# "39 of 39 killed; 39 died on their own guard's ASSERTION" · C3 byte-identical. ~30-90s.

# 3. the asks block itself, on a real in-population PR (its own commits carry the trailer)
python3 $DEVRC/scripts/audit-dispatch.py 1901 --repo innovation-upstream/devrc --round 0 \
  | awk '/THE OPERATOR.S OWN ASKS/,/^\*\*Ledger/'
# expect the quoted asks + a Sources block. 🔴 Do NOT publish a quoted ask anywhere.

# 4. the gate the merge actually rests on — and gate the MERGED tree, never the branch
nix build $DEVRC#checks.x86_64-linux.pytests --no-link -L   # read RESULT:/SCOPE:, not the rc
# ⚠ a SILENT build is the CACHED case, not a pass: recover via
#   nix path-info --derivation … → nix log "$DRV"  (guard $DRV for emptiness), wc -c first.

# 5. the false positive #1914 declined to carve out — confirm it is still a false POSITIVE
nix develop $DEVRC -c python3 -c 'import sys; sys.path.insert(0,"'$DEVRC'/scripts"); \
from testlib import public_ip_scan as m; print(len(m.find_in_line("x = parts[1::2]")))'
# expect 1 — the gate reports it. That is deliberate: spell the slice differently.
```
