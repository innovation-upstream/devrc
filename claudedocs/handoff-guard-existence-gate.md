# Handoff: guard-existence-gate — 2026-09-20

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
Stop the handoff write-back Stop hook arming on `claudedocs/handoff-*.md`-shaped
STRINGS that name no real document — it demanded a handoff for `handoff-x-y.md` and
`handoff-same.md`, both fixture strings in `scripts/tests/test_find_session_arc.py`.
🔴 **A NEW ARC.** Its parent, `handoff-find-session-arc-resolution.md`, is CLOSED and
FROZEN AT ROUND 1; this was that doc's rank 1 and is not another round of it.
- **closing-condition:** `check` — `#1799` merged, both hosts converged
  (`scripts/ship.sh`), and the probe under **How to verify** prints
  `armed=[]` for a doc that does not exist while printing a path for one that does.
  🔴 FROZEN AT ROUND 1.

## State now
- **`#1811` head is `41cef3db`** — round 2's four fixes landed and are pushed. Tests at that
  sha on a clean tree: **3,298 passed** (hook tests + `test_handoff_doc_size.py`) and **101
  passed** (guard module alone), both identical to the round-2 baseline at `b599278c`.
- 🔴 **CI is PENDING on `41cef3db`** (all four re-queued 17:13Z by the fix commit),
  `mergeStateStatus=UNSTABLE`. It was **green on `b599278c`** at 06:56Z — that green is a
  claim about the PREVIOUS head and does not transfer. Re-read before merging.
- 🔴 **THE LADDER IS STOPPED BY OPERATOR DECISION — not by a mechanism firing.** Round 2
  returned findings, so the findings-keyed rule says another round. The operator chose to
  fix the four and stop. **Neither documented stop mechanism actually applied** — see the
  Gotchas entry; recording it as a criterion that fired would be the exact class of false
  claim this ladder spent three rounds removing.
- **`#1799` (the parent) is MERGED (`a371da4e`) and DEPLOYED to both hosts.** Its closing
  condition was re-run LIVE this session, not taken from the doc: deployed copy
  `/nix/store/py0j13ik…-hm_handoffwriteguard.py`, `_read_off_a_ref` = 6; probe returns `[]`
  for an absent doc and a path for a real one, with the positive control firing. **ADDRESSED
  — that arc is CLOSED.**
- **`#1811` is NOT deployed.** The hook is a `home.file` copy, so the sequence is merge →
  pull → `ship.sh` → confirm the deployed copy has no `GIT_VERB_SCAN_CAP`. Until then both
  hosts run `#1799`'s version.
- Branch is **44 commits behind `main`** (merge-base `b289a9fe`); `strict` is false, so the
  green is a claim about the branch, never about the tree the merge creates.
- **Still no `clawgate-task:` field, and the reason CHANGED.** `clawgate_handoff.sh resolve`
  now exits **5 — NOTHING RESOLVED, 0 tasks for this session** (the earlier doc recorded one
  `role=read` task, `#321`). An unknown session id also answers 200 with an empty array, so
  this zero cannot distinguish "touched no task" from "wrong id". Not a clean bill of health.

## Open investigations — live diagnosis state

### `main` is RED, reproduced — an opencode version pin, reachable from no diff
- as-of: 2026-09-20
- **Symptom + exact repro:** `main-green-check` failed BOTH attempts and exited 10.
  ```bash
  systemctl --user show main-green-check.service -p ExecMainStatus -p Result
  journalctl --user -u main-green-check.service -n 25 --no-pager -o cat
  grep -nE 'FAILED|AssertionError' ~/.cache/main-green/logs/pytests.attempt2.log
  ```
- **Observed (with values):** on `52939157`, `pytests rc=1 verdict=red` on attempts 1
  AND 2; `nodetests` and `gotests` green both times. The pytest tier reported
  `TOTAL collected=24011 passed=24006 skipped=4 failed=1`. The single failure is
  `scripts/tests/test_opencode_engine.py:745` —
  `AssertionError: opencode on PATH is '1.18.30', but every 'measured on v1.18.29'
  claim in scripts/opencode/opencode.jsonc, scripts/opencode/README.md and
  scripts/tests/test_opencode_config.py is keyed to '1.18.29'`; `assert '1.18.30' ==
  '1.18.29'`. `main` has since moved to `cee56910` then `a67db5b5`.
- **Ruled out:** that it is caused by any recent diff — the failing assertion compares
  the `opencode` binary ON PATH against a literal pinned in three tracked files, so it
  moves when the BINARY is upgraded, not when code changes. via: code
- **Ruled out:** a load flake — the deadman re-ran and reproduced it, which is the
  discriminator that rc 10 exists to make. via: measurement
- **Leading hypothesis:** an `opencode` upgrade to 1.18.30 landed on the workbench and
  the three pinned `v1.18.29` claims were not re-keyed. The fix is to re-measure each
  claim against 1.18.30 and move the literals — NOT to loosen the assertion, which is
  the thing that makes the claims dateable.
- **Next probe:** `opencode --version` on BOTH hosts, then
  `git -C ~/workspace/devrc grep -n '1\.18\.29' -- scripts/opencode/ scripts/tests/`
  to enumerate every literal that must move together. Check the laptop too: if the two
  hosts disagree, the pin is host-dependent and that is a second finding.

### The cap-inert mutant hangs the suite instead of failing it
- as-of: 2026-09-20
- **Symptom + exact repro:** with `GIT_VERB_SCAN_CAP = 10**9` in a scratch copy, a filtered
  pytest run of `test_the_scan_cap_BOUNDS_the_search` does not terminate (killed at 150 s and
  again at 10 min). With the cap at 4096 the same filtered run is **1.5–1.7 s**.
  ```bash
  sed -i 's/^GIT_VERB_SCAN_CAP = 4096$/GIT_VERB_SCAN_CAP = 10**9/' <scratch>/handoff-write-guard.py
  PYTHONDONTWRITEBYTECODE=1 nix develop ~/workspace/devrc -c python3 -m pytest \
    <scratch>/scripts/claude-hooks/tests/test_handoff_write_guard.py -q -k scan_cap
  ```
- **Observed (with values):** the sibling mutant `head = cmd[:start]` IS killed, by this
  test's own assertion (`AssertionError: assert ['/tmp/nix-sh…age-audit.md'] == []`). Isolated
  regex timings on the same 12,316-byte head show **no blowup**: `REF_PREFIX_RX` 0.03 ms,
  `GIT_OBJECT_READ_RX` 0.03 ms, capped or not. So the hang is NOT in the two regexes the cap
  guards, which is the whole puzzle.
- **Ruled out:** that the test is simply vacuous — the verdict provably flips on pad length
  alone (121 B head ⇒ exempt, 12,316 B ⇒ not). via: measurement
- **Ruled out:** an in-process probe showing `handoff_read_docs -> []` under the inert cap
  (which would mean SURVIVED). **That probe was WRONG**: it passed `-C /tmp/er`, a directory
  that does not exist, so `_resolve` failed on the DIRECTORY and never reached the cap. A
  probe that fails for the wrong reason reads exactly like a result. via: code
- **Leading hypothesis:** something on the arming path other than the two regexes is
  superlinear in head length — `COMMENT_PAT`/`QUOTED_PAT` substitution over a 12 KB command,
  or `_bases`/`DASH_C_RX`. The cap masks it by shortening the head, which would mean the cap
  buys MORE than the comment claims.
- **Next probe:** time each stage separately on the 12 KB command with the cap inert —
  `re.sub(COMMENT_PAT, …)`, `_bases`, `HANDOFF_PATH_RX.finditer`, then `_read_off_a_ref` —
  using a real existing repo dir for `-C` so `_resolve` is actually reached. Whichever stage
  dominates is the answer, and it decides whether the cap's comment is understated.

### ✅ RESOLVED — "The cap-inert mutant hangs the suite instead of failing it"
- as-of: 2026-09-20
- **The block above this one is CLOSED and its leading hypothesis was WRONG.** It guessed
  at "something on the arming path other than the two regexes is superlinear in head
  length — `COMMENT_PAT`/`QUOTED_PAT` or `_bases` — which would mean the cap buys MORE
  than the comment claims". Round 0 of `#1811` found the real cause and it is much dumber.
- **Answer:** `test_the_scan_cap_BOUNDS_the_search` built its padding as
  `"A" * (guard.GIT_VERB_SCAN_CAP * 3 + 7)`. Under the mutant `GIT_VERB_SCAN_CAP = 10**9`
  that is a **3,000,000,007-byte string**. The hang was the FIXTURE allocating 3 GB, not
  the code under test. Nothing in the hook is superlinear the way the block supposed.
- 🔴 **THE CLASS, WHICH IS THE PART WORTH KEEPING:** a fixture parameterised on the very
  constant being mutated can never observe that mutation — it scales with it. This is
  `claude/RULES.md`'s "ISOLATE THE MUTATION" trap, and it produced a result that looked
  like a deep finding (an unexplained hang) rather than like a broken harness.
- **Why it no longer matters either way:** `#1811` deletes `GIT_VERB_SCAN_CAP`, its
  truncation and that test outright, so there is no cap left to pin and no mutant left to
  settle. The question was retired by deleting its subject.

## Next steps (ranked)
1. **Merge `#1811` and ship both hosts.** Wait for CI on `41cef3db` (pending at time of
   writing), merge `--squash`, then verify **by CONTENT, never ancestry** — a squash is
   never an ancestor of its base — then `scripts/ship.sh` and confirm the deployed copy has
   no `GIT_VERB_SCAN_CAP` with `readlink -f` as the arbiter, never a diff. Post the round-2
   `audit-claims` block as an ISSUE comment first (`--round 2 --audited b599278c --payload
   88`); a block posted as a REVIEW is invisible to the next round's assembler.
   forcing: gate — a fleet-wide Stop hook, firing after every tool call of every session,
   sitting unmerged with three audit rounds paid for.
2. **Operator call: lift `test_the_hook_spawns_no_subprocess_on_any_path`?** An OBSERVATION
   from `#1092`'s body frozen into a prohibition, wider than the `shutil` standard the same
   file uses for the same hot path. Lifting it allows `git cat-file -e <ref>:<path>`, making
   the ref exemption VERIFIED instead of shape-matched. 🔴 **The evidence for this got
   stronger: the residual fired a SECOND time, on a real session, on 2026-09-23** — see the
   Gotchas entry. It is no longer hypothetical.
   forcing: user — reopens the fix shape the operator originally chose; not mine to take.

## Gotchas / decisions / dead-ends
- 🔴 **THE APPROVED FIX WAS NARROWED AFTER AN EXISTING GUARD FORBADE IT, AND THE
  NARROWING IS THE INTERESTING PART.** The operator chose "existence OR
  `git cat-file -e <ref>:<path>`". That variant cannot be built:
  `test_the_hook_spawns_no_subprocess_on_any_path` asserts the SOURCE contains no
  `import subprocess`, on a hook that fires after every tool call of every session.
  So `_read_off_a_ref` checks the COMMAND SHAPE instead — a `<ref>:` immediately
  before the match plus a git object-read verb in the same segment. **Check the
  constraints a fleet-wide hook already declares BEFORE proposing a fix shape to the
  operator**; the option presented was unbuildable and the discovery came after the
  approval.
- ⚠ **What the exemption does NOT cover, stated so it is not rediscovered as a bug:**
  `git show <anything>:claudedocs/handoff-<anything>.md` still arms whether or not that
  ref or that doc exists. Far narrower than the over-match removed — neither observed
  false positive carries a ref prefix at all — but it is an over-match, not its
  absence. Paying a subprocess to close it is an operator decision nobody has made.
- 🔴 **`[^;&|\n]*?` INSIDE A REGEX DOES NOT SCOPE IT TO A COMMAND SEGMENT.** The first
  `GIT_OBJECT_READ_RX` was `\bgit\b[^;&|\n]*?\b(?:show|cat-file)\b` searched over the
  WHOLE command, on the theory the negated class kept the match inside one segment. It
  does not: `git show HEAD; cat host:claudedocs/x.md` matches `git show` in the FIRST
  segment and the second segment's path borrows the exemption. The segment must be
  SPLIT OUT first (`SEGMENT_SPLIT_RX.split(head)[-1]`). Caught by a case written in the
  same diff because the hole was imaginable — and it was real.
- 🔴 **A FIXTURE THAT DOES NOT EXIST CAN MAKE ANOTHER TEST VACUOUS, SILENTLY.**
  `test_a_path_after_a_hash_is_a_comment_not_a_read` discriminates by RESULT: it names
  two docs, one after a `#`, so a mutant skipping comment-stripping returns TWO. Under
  the new existence gate, if `handoff-y.md` were absent from disk that mutant would
  return ONE and the test would pass with the comment strip DELETED. The fixture now
  creates it, with the reason written beside it. **When you add an existence gate, ask
  which other test's discriminating power depended on things NOT existing.**
- 🔴 **FRESH FIXTURE FILES SATISFIED THE GUARD THEY WERE MEANT TO ARM.** `doc_state`'s
  third satisfaction route is the doc's OWN mtime (`getmtime(doc) >= read_at`), so
  writing the fixture docs at test time made them written-after-the-read: six Stop-gate
  cases went `block` -> `silent`, green for the wrong reason had the assertions been
  weaker. The fixture stamps `os.utime(p, (BEFORE_READ_EPOCH, ...))`.
- **Two new tests are INVARIANT GUARDS, not regression coverage, and are labelled so in
  their own docstrings** — they pass at `cee56910` too. What proves the ref exemption
  REACHABLE at HEAD is mutation M1, not those tests.
- **Decision: the Read arm's `claudedocs/` requirement rode along in the same PR.** It
  is a second behaviour change, kept because it closes a real asymmetry — the Bash
  arm's own non-match table already declares `cat docs/handoff-format.md` a non-read,
  yet the same path armed through `Read` and no `Write` could ever satisfy it, since
  `is_handoff_write` requires `claudedocs/`. Pinned by its own test and mutation M3.
- ⚠ **An earlier framing in this session was imprecise and is corrected here:** the two
  arms were said to be asymmetric on `claudedocs/` generally. `HANDOFF_PATH_RX` has
  always required it on the Bash side; only the **Read** arm was affected.
- **The guard fired CORRECTLY at the end of this session** — on
  `handoff-find-session-arc-resolution.md`, a doc that exists, after real work. That is
  the behaviour the fix preserves, and it is the reason this doc exists.

- 🔴 **A SQUASH MERGE IS NEVER AN ANCESTOR OF ITS BASE, AND THIS RUN DEMONSTRATED IT.**
  `git merge-base --is-ancestor d7af847c origin/main` returned **FALSE** immediately after a
  successful merge. Trusted, it reads as "not merged — redo the work". Verified by CONTENT
  instead (`_read_off_a_ref` 6, `GIT_VERB_SCAN_CAP` 2, the new pin present on `main`).
- 🔴 **WAITING FOR CI WAS THE WHOLE VALUE, AND THE RED WAS NOT MINE.** Merging on the
  pending state would have shipped with the one gap all three audit rounds named still open.
  The red resolved to a test the diff cannot reach — attribute a shared red by the FAILING
  TEST, never by the job name or the colour.
- 🔴 **MY OWN POSITIVE CONTROL WAS WRONG ON ITS FIRST RUN and returned a reassuring `[]`.**
  It named `claudedocs/handoff-guard-existence-gate.md` — a doc that lives on the UNMERGED
  `#1800` and is not on disk. So the control agreed with the defect probes for a completely
  different reason. Caught only because a control that cannot go non-empty is not a control.
- **Decision: the residual over-match ships knowingly.** `git show <anything>:claudedocs/
  handoff-<anything>.md` arms whether or not the ref or the doc exists. Stated on
  `_read_off_a_ref`, and rank 2 is the decision about closing it.

- 🔴 **THE LADDER'S DURABLE MEASUREMENT, MOVED HERE BECAUSE A REPLACE ALMOST ATE IT.** Round 3
  established that the shipped executable delta is **behaviour-neutral across 17,978 real
  corpus commands** — `_read_off_a_ref`'s verdict differs from the pre-round-1 spelling on
  **zero** of them — and that the base-selection argument holds analytically AND empirically
  (400-combination differential fuzz, 0 violations; 17,981 payloads: silent→arm **0**,
  arm→silent 1,187, different-doc 46). Those numbers are the evidence the merge rested on.
  They were living under `## State now`, which is a REPLACE heading, and `handoff_doc.py`
  warned `DROPS 1 line(s) that look DURABLE` on the very next update. **A measurement that
  justifies a decision belongs under an APPEND heading the moment it is made** — status goes
  in `State now`, evidence does not.

- 🔴 **RANK 3 IS DELETED AND THE PREMISE WAS BACKWARDS.** It read "re-key the three
  `v1.18.29` claims — NOW BLOCKING EVERY PR IN THE REPO". `opencode 1.18.30` is a BROKEN
  upstream build (every prompt dies in `SystemPrompt.environment`; fixed upstream in
  1.18.31), and `flake.nix` pins 1.18.29 through a deliberate overlay to avoid it. The red
  was the pin WORKING: the branch predated `#1804`, so its sandbox built the broken binary.
  Re-keying would have edited 33 sites to assert that the deployed binary is the one that
  cannot run a prompt — which `HISTORICAL_VERSION_CLAIMS` warns about in those words.
  `main` is green on all four checks and both hosts run 1.18.29.
  ⚠ **The trap was a STALE SELF-OBSERVATION**: both hosts genuinely read 1.18.30 when
  measured early in the session, and this session's own `ship.sh` is what deployed the pin.
  A fact gathered hours earlier by me was treated as current at the moment of acting.
- 🔴 **A DELETION PR IS A GOOD PLACE TO HIDE A FALSE CLAIM, AND ITS TITLE IS WHERE IT
  HID.** `#1811`'s title said it deleted "the scan cap, its test **and its ranked
  follow-up**". The diff touched two code files and never this document, so rank 1 survived
  on `main` pointing at a constant the same PR removed. Found by round 0, not by the author.
  **A claim in a title is a claim.**
- ⚠ **The cap was retired on the WRONG OPERAND'S numbers.** The argument was "the largest
  SEGMENT ever scanned is 296 B against a 4096 B cap" — 296 B is exact, and irrelevant: the
  cap truncated the HEAD. Re-derived **2026-09-23** over **6,621** transcripts
  (`~/.claude/projects/*/*.jsonl` + `*/*/subagents/*.jsonl`), scanning each Bash command
  exactly as `handoff_read_docs` does — `COMMENT_PAT`-stripped, then `HANDOFF_PATH_RX`:
  **23,964 match sites, head max 27,856 B, p99 3,144 B, 130 sites over the 4,096 B cap**
  (restricted to the handoff-basename sites the guard acts on: 20,793 sites, **98** over
  the cap).
  🔴 **RETRACTED IN PLACE — this bullet used to end "The deletion is still right, but
  because the scans are now linear — never because the input is small." THAT IS DRAFT 2,
  AND IT IS REFUTED FURTHER DOWN THIS SAME LIST** (see *"THE QUADRATIC DID NOT LEAVE, IT
  MOVED"*). The scans are linear PER CALL; the conclusion is about the COMMAND, which is
  O(n²). It stood here as a bare assertion ABOVE its own refutation with no marker, so a
  top-down reader met the wrong version first — which is why the retraction is written
  here rather than only where the refutation lives. The argument that survives is
  *"THE ARGUMENT THAT SURVIVES: THE CAP NEVER DELIVERED WHAT IT WAS CREDITED WITH"*.
  ⚠ **The round-1 figures this bullet carried — `29,039 B` / `108 sites` / `21,291 sites`
  / p99 `3,111 B` — are replaced, and the discrepancy is METHOD, not corpus drift.**
  Measuring both ways over the same corpus minutes apart: head max is **29,039 B** over
  the RAW command and
  **27,856 B** over the COMMENT-STRIPPED one the guard actually scans. Round 1 quoted the
  raw number for a scan that never sees it. (Round 2's auditor independently got 27,856 B /
  114 sites; the 114 did not reproduce here — I get 130 over all sites, 98 over acted-on
  ones. Corpus grows daily, so treat the site count as dated, never as a constant.)
  As first written the argument would have licensed deleting the cap WITHOUT the
  changes that make it safe.
- 🔴 **THE SANDBOX TIER WAS THE GAP EVERY AUDIT ROUND NAMED, AND CLOSING IT IS WHAT THE
  MERGE RESTED ON — carried here because it is EVIDENCE, and `State now` is a REPLACE
  heading that had already been warned for dropping it once.** All three rounds of
  `#1799` verified the dev-host tier only and each said so. `tekton/devrc-pytests` on
  `d7af847c`: **`collected=24037 passed=24032 skipped=4 failed=1`**, the single failure
  being the inherited opencode pin, unreachable from a diff touching only
  `scripts/claude-hooks/**`. `cairn-client-runs`, `gotests` and `nodetests` all success.
  Waiting ~20 minutes for that verdict is the only reason the merge was not blind.

- 🔴 **THE QUADRATIC DID NOT LEAVE, IT MOVED — AND I DELETED THE COMMENT THAT SAID SO.**
  The scans are linear PER CALL; `_read_off_a_ref` runs once per `HANDOFF_PATH_RX` match, so
  k matches x a full-head scan is O(n²) per COMMAND — round 1 measured **27.9x at k=6,400 /
  261 KB**. The deleted comment had it exactly right ("IS PER CALL, NOT PER COMMAND … what
  the cap buys is the complexity CLASS"). **Three drafts of that justification, each
  falsified by the next round:** wrong operand → per-call-licensing-per-command → the one
  that survives. Reaching for a better reason is what regenerates the error.
- 🔴 **THE ARGUMENT THAT SURVIVES: THE CAP NEVER DELIVERED WHAT IT WAS CREDITED WITH.** It
  did not bound `HANDOFF_PATH_RX` (`:287`), which is ITSELF O(n²) on a long path-class run
  and dominates the hot path. MEASURED on `git show <64 KB of 'a'>:claudedocs/handoff-x.md`:
  **1,598 ms WITH the cap, 1,636 ms without** — no material difference. ⚠ That also bounds
  how strong ANY perf claim about this file can be, and the `HANDOFF_PATH_RX` quadratic is
  pre-existing in BOTH trees — unreached, unfixed, and NOT this arc's to close.
- 🔴 **"I WROTE THE CORRECT FIX AND REVERTED IT" — RETRACTED. The fix was never shown to be
  correct, and the `\Z` argument for it is WRONG BY SCOPE.** This bullet claimed a
  bounded-tail for `REF_PREFIX_RX` "is answer-preserving (it is `\Z`-anchored, so only the
  trailing token can match)". `\Z` covers **one of the three** scans `_read_off_a_ref` runs;
  `SEGMENT_SPLIT_RX.split(head)[-1]` and the `GIT_WORD_RX`/`GIT_VERB_RX` pair read the head
  from the LEFT. Counter-example, measured 2026-09-23 —
  `head = "git show " + "z"*5000 + " ref:"` gives **True on the full head, False on
  `head[-4096:]`** (the `git show` is truncated away). The boundary is exact: it diverges at
  `pad=4083`, the first head longer than the 4,096 B window — i.e. ANY head longer than the
  bound can hide the verb, so no bound is safe.
  And bounding `REF_PREFIX_RX` **alone** — all the `\Z` argument licenses — does not deliver
  the fix either: at a 64 KB head it costs **0.137 ms**, the segment+git scans **0.138 ms**,
  and `HANDOFF_PATH_RX` **2,193 ms**. So: bound the whole head and lose answers, or bound the
  cheap scan and save 0.137 ms of 2,193. **No bounded-tail fix is currently known to be both
  sufficient and answer-preserving**, and the in-source note no longer tells anyone to reach
  for one — re-deriving it from `\Z` regenerates the deleted cap's own bug. Reverting it was
  still right (corpus max 18 matches/command, 33,673 B, PostToolUse-only), but for reach, not
  for correctness. **This is the fourth draft of this justification; the first three were each
  falsified by the next round. "No argument is known to work" is the finding.**
- 🔴 **THE "FAIL-SAFE" EXCUSE HAS A FLOOR, NOW NAMED.** "A blown hook loses an ARMING
  (silent, fail-safe) rather than hanging a turn" holds only ABOVE the CLI's hook timeout;
  below it there is no lost arming and no silence, just turn latency on a hook that fires
  after every tool call. Measured 2026-09-23, uncapped vs a 4,096 B head cap, **verdicts
  identical throughout**: k=200 10.4 vs 8.9 ms (1.2x), **k=400 39.6 vs 20.0 ms — divergence
  starts here**, k=1,600 659 vs 91 ms (7.3x), k=6,400 12,653 vs 384 ms (33x). Corpus max is
  k=18, so incidence today is zero — but the "if reach ever changes" pointer now has its
  threshold, **k≈400**, instead of none. ⚠ Best of 3 up to k=1,600, single run above; the
  milliseconds are load-dependent, the RATIO and the k≈400 knee are the claim.
- 🔴 **"CORPUS INCIDENCE IS ZERO" WAS FALSE AND THE MISS HAD A MECHANISM — BUT ROUND 1'S
  REPLACEMENT, A BARE "IT IS 2", IS RETRACTED TOO.** Not as false: as unquotable. It shipped
  with no method, no date and no corpus size, in an arc whose own source file says
  in those words that such a count "cannot be re-checked". Re-derived **2026-09-23** over
  **6,621** transcripts (`projects/*/*.jsonl` + `projects/*/*/subagents/*.jsonl` — ⚠
  subagents are at **DEPTH 4**; a `*/subagents/*.jsonl` glob matches **zero** of the 5,628
  and still prints a confident total, which bit this round's first pass), counting sites the
  WIDE `[^\s:]+` class would exempt and this class denies:
  **6 sites across 3 sessions — 5 of them THIS ARC'S OWN probes, exactly ONE genuine**
  (2026-09-15, session `f0decd34`,
  `subprocess.run(['git','-C',R,'show',ref+':claudedocs/handoff-tmux-webapp.md'])`, token
  ending in `'`). The load-bearing half survives: not zero, and the narrowing has cost a real
  arming once.
  🔴 **THE COUNT INFLATES ITSELF — one of the 2026-09-20 sites IS the `gh pr edit` that
  published the previous count.** Writing the number down incremented it. Any re-derivation
  must state the arc-own split or it will read self-inflation as real growth.
  It is also not "the QUOTED COMPUTED ref": ordinary shell QUOTING supplies the trailing
  quote, so a quoted LITERAL flips too. All ten spellings are now pinned.
- 🔴 **ROUND 0 SOLVED THE MUTANT THIS ARC HAD LEFT OPEN, AND THE ANSWER WAS A BROKEN
  HARNESS.** `test_the_scan_cap_BOUNDS_the_search` built its pad as
  `"A" * (GIT_VERB_SCAN_CAP * 3 + 7)`; under `CAP = 10**9` that is a **3,000,000,007-byte
  string**. The hang was the FIXTURE allocating 3 GB. A fixture parameterised on the constant
  being mutated SCALES WITH IT and can never observe the mutation. It also refuted the
  recorded hypothesis (hidden superlinearity in `COMMENT_PAT`/`_bases`) which, had it been
  true, would have made the deletion unsafe.
- 🔴 **A CLAIM IN A TITLE IS A CLAIM.** `#1811`'s title said it deleted "the scan cap, its
  test **and its ranked follow-up**"; the diff touched two code files and never this doc, so
  rank 1 survived on `main` pointing at a constant the same PR removed. Found by round 0, not
  by the author. Fixed in `f97aa7c9` — in the PR that caused it.
- ⚠ **`~/workspace/devrc` IS NOW OFF-LIMITS FOR WRITES** (CLAUDE.md, 2026-09-20): no
  `commit`/`add`/`checkout`/`switch`/`stash` in the primary clone — every change goes through
  a throwaway worktree off `origin/main`, because a checkout there changes LIVE behaviour on
  this host (every `mkOutOfStoreSymlink` target resolves into that tree, `claim-work`
  included). All work in this arc already used worktrees; the rule is recorded because the
  next session will want to write the doc and must not do it in the primary clone.

- ✅ **THE SANDBOX TIER — the gap EVERY round of BOTH ladders named it could not speak for —
  WENT GREEN, and here is the history so it survives the next status replace.** All four
  checks (`pytests`, `nodetests`, `gotests`, `cairn-client-runs`) reported `success` on
  `ca5e1eea` at **2026-09-20T20:05Z**, and again on `b599278c` at **2026-09-23T06:56Z**
  (`pytests collected=23818 passed=23814 failed=0`; `nodetests 1608/1608`; `gotests 386/386`).
  🔴 **Each green is a claim about THAT head only.** Measured twice this arc: pushing the
  docs commit that RECORDED the green re-queued all four and put the PR back to `UNSTABLE`,
  and the round-2 fix commit did it again. **Writing the green down is what invalidates it** —
  re-read the statuses on the head you are actually about to merge.
- 🔴 **THE STOP WAS AN OPERATOR DECISION AND NEITHER DOCUMENTED MECHANISM APPLIED — written
  down because claiming otherwise is the failure this ladder exists to catch.** (a) The
  **prose escape hatch** governs "a PR whose WHOLE DIFF is prose, never merely one whose
  PAYLOAD is"; round 2's delta carries an executable test change (the 3→10 parametrization
  widening), so `#1811` is outside that population by the rule's own words. (b) The
  **attribution gate** fires on two consecutive rounds changing zero PAYLOAD lines; round
  2's fixes touch comments inside `handoff-write-guard.py`, which is the payload file, so
  the count is 88 and non-zero. What IS true, measured both rounds: **zero EXECUTABLE
  payload lines moved in either round** — `git diff <range> -- scripts/claude-hooks/handoff-write-guard.py
  | grep -E '^[+-]' | grep -vE '^[+-][[:space:]]*#'` returns EMPTY for `680826a5..b599278c`
  (60 lines) and for `b599278c..41cef3db` (88 lines). Rounds 0, 1 and 2 found **zero code
  defects** between them. That is the honest basis for stopping, and it is a different claim
  from "the gate fired".
- 🔴 **THE DECLARED RESIDUAL OVER-MATCH FIRED ON A REAL SESSION — SECOND OBSERVED INSTANCE,
  2026-09-23, and it was this session's own verification probe.** Running the doc's own
  **How to verify** block armed the Stop guard on `handoff-no-such-doc.md`, a doc that has
  never existed, because the probe line is
  `git -C ~/workspace/devrc show HEAD:claudedocs/handoff-no-such-doc.md` — a `<ref>:` prefix
  plus a git object-read verb. Dismissed with
  `handoff-write-guard.py --dismiss handoff-no-such-doc.md --session <id>`. 🔴 **The doc's
  own verification procedure trips the residual the doc declares**, so every session that
  follows it pays one false arming. Rank 2 is the decision about closing it.
- 🔴 **`resume-state.sh` COMPARES ONLY AGAINST `origin/main`, so an arc whose doc lives on
  its OWN OPEN PR reads as current when it is not.** This session opened with
  `handoff-read: working-tree copy (identical to origin/main)` and `DRIFT (none detected)` —
  both true, and both misleading: the authoritative copy was the **+93/−40 rewrite on the
  unmerged branch** (`b599278c`), which already carried the `✅ RESOLVED` block retiring the
  cap-inert investigation and a different ranked list. The tell is a `docs(handoff)` commit
  in `git log origin/main..origin/<branch>`. **Check whether the arc's own PR rewrites its
  handoff before trusting a clean DRIFT.**
- 🔴 **A COUNT CAN INFLATE ITSELF, AND THIS ONE DID — the `gh pr edit` that PUBLISHED the
  previous count is one of the sites it counts.** The "N real sites flip" figure rises every
  time this arc writes it down. Re-derived 2026-09-23: **6 sites across 3 sessions, of which
  5 are this arc's own and exactly ONE is genuine** (2026-09-15, session `f0decd34`, a real
  `git show <ref>:claudedocs/handoff-tmux-webapp.md` from another session). Round 2's brief
  said "4 genuine", which reads as four real-world sites when only one is. The claim now
  carries method, date and corpus size in all four copies (source, test docstring, doc, PR
  body) — the rule the same file states at `:1004-1008` and that this count had not been
  given.
- 🔴 **TWO FIGURES DISAGREED FOR A METHOD REASON, NOT A DRIFT REASON — do not "resolve" a
  number before asking what each side measured.** The doc's `29,039 B` and the auditor's
  `27,856 B` are the SAME corpus measured differently: the guard scans the **comment-stripped**
  command (`:1024`), round 1 measured the **raw** one. Both reproduce, 29,039 raw / 27,856
  stripped. Treating either as stale would have written a third wrong number.
- ⚠ **Three inherited figures did NOT reproduce and are now dated rather than inherited:**
  the site count is **130** over all match sites / **98** restricted to handoff-basename
  sites the guard acts on (neither the round-1 `108` nor the round-2 `114`);
  `REF_PREFIX_RX` costs **0.137 ms** at a 64 KB head, not `0.98 ms` (the conclusion is
  unaffected and in fact stronger, since `HANDOFF_PATH_RX` is 2,193 ms). Two figures were
  deliberately NOT re-derived and are flagged as such in the PR body so they are not read as
  re-confirmed: the `1,598 / 1,636 ms` cap-vs-no-cap pair, and round 1's `27.9× at k=6,400`
  (a different quantity from the re-measured 33.0×, which is uncapped-vs-capped).
- 🔴 **NO BOUNDED-TAIL FIX IS KNOWN TO BE BOTH SUFFICIENT AND ANSWER-PRESERVING, and the
  source now says exactly that rather than reaching for a fourth justification.** The
  previous note claimed the fix "IS available and is answer-preserving" because
  `REF_PREFIX_RX` is `\Z`-anchored — true of ONE of the three scans in `_read_off_a_ref`;
  `SEGMENT_SPLIT_RX.split(head)[-1]` and the `GIT_WORD`/`GIT_VERB` pair are not tail-bound-safe.
  Counter-example, reproduced and now runnable in **How to verify**: head
  `"git show " + "z"*5000 + " ref:"` → full head `True`, `head[-4096:]` `False`. The boundary
  is **pad=4083**, the first head longer than the window — i.e. the instant `git show` falls
  out of it, so ANY bound can hide the verb. The "reach for it if reach ever changes" pointer
  was DELETED rather than re-aimed: a maintainer taking it up would have re-derived the
  deleted cap's bug.
- 🔴 **THE FAIL-SAFE FRAMING HAS A FLOOR AND NOW STATES IT.** "A blown hook loses an ARMING
  (silent, fail-safe) rather than hanging a turn" holds only ABOVE the CLI's hook timeout.
  Below it there is no lost arming and no silence — there is unbounded turn latency on a hook
  that fires after every tool call. Measured, cap vs no cap, verdicts identical at every k:
  k=200 → 10.4/8.9 ms (1.2×) · **k=400 → 39.6/20.0 ms, the knee** · k=1,600 → 659/91 ms
  (7.3×) · k=6,400 → 12,653/384 ms (33×). Corpus max is k=18, so incidence today is zero.
- 🔴 **A GLOB THAT MISSES A WHOLE DIRECTORY LEVEL RETURNS A CONFIDENT ZERO, NOT AN ERROR.**
  The round-2 fix pass first globbed `projects/*/subagents/*.jsonl` and matched **zero** of
  5,626 subagent transcripts — they sit at **depth 4** — while reporting a total computed off
  993 files. Every corpus scan now asserts a NON-EMPTY subagent set as a positive control.
  Same family as the `grep -r`/`.gitignore` blindness: the answer is a claim about the
  instrument's VIEW, never about the corpus.

## How to verify
```bash
# #1811's state and its sandbox tier — the tier both ladders could not speak for
gh pr view 1811 --repo innovation-upstream/devrc --json state,mergeable,mergeStateStatus
S=$(gh pr view 1811 --repo innovation-upstream/devrc --json headRefOid --jq .headRefOid)
gh api "/repos/innovation-upstream/devrc/commits/$S/statuses" \
  --jq 'group_by(.context)[]|max_by(.created_at)|"\(.context)=\(.state)"'

# the guard module on the PR branch
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-algo/scripts/claude-hooks/tests/test_handoff_write_guard.py -q   # 101 passed

# round 2's counter-example: a bounded tail is NOT answer-preserving. Prints True/False.
nix develop ~/workspace/devrc -c python3 -c '
import importlib.util as u
P="/home/zach/workspace/devrc-algo/scripts/claude-hooks/handoff-write-guard.py"
s=u.spec_from_file_location("g",P)
g=u.module_from_spec(s); s.loader.exec_module(g)
def p(h):
    if not h.endswith(":") or not g.REF_PREFIX_RX.search(h): return False
    seg=g.SEGMENT_SPLIT_RX.split(h)[-1]; m=g.GIT_WORD_RX.search(seg)
    return bool(m) and bool(g.GIT_VERB_RX.search(seg,m.end()))
h="git show "+"z"*5000+" ref:"; print(p(h), p(h[-4096:]))'   # -> True False

# the corpus numbers (6,621 transcripts / 6 flip sites / head max 27,856 B). ⚠ subagents
# are at DEPTH 4 — `*/subagents/*.jsonl` matches ZERO and still prints a total. Assert it.
#   files = glob('~/.claude/projects/*/*.jsonl') + glob('~/.claude/projects/*/*/subagents/*.jsonl')
#   assert len(files) > 6000        # positive control; a bare zero is not a measurement
# then, per Bash tool_use command containing 'claudedocs/':
#   stripped = re.sub(COMMENT_PAT, " ", cmd); HANDOFF_PATH_RX.finditer(stripped)
# and count sites where the WIDE [^\s:]+ ref class exempts but REF_PREFIX_RX does not.

# the cap really is gone from the branch, and still present in the DEPLOYED copy
grep -c GIT_VERB_SCAN_CAP ~/workspace/devrc-algo/scripts/claude-hooks/handoff-write-guard.py  # comments only
grep -c GIT_VERB_SCAN_CAP "$(readlink -f ~/.claude/hooks/handoff-write-guard.py)"             # >0 until shipped
```
