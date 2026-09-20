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
- ✅ **`#1799` MERGED (`a371da4e`) AND DEPLOYED TO BOTH HOSTS**, verified at the consumer:
  store path moved, `_read_off_a_ref` 0 → 6, and the original symptom reproduced-and-gone
  with a positive control beside it. Sandbox tier on the merged head:
  `collected=24037 passed=24032 skipped=4 failed=1`, that one failure inherited.
- **`#1811` OPEN** — `/the-algorithm` steps 1–3 applied to what the ladder left behind.
  DELETES `GIT_VERB_SCAN_CAP`, its truncation and its test; makes `REF_PREFIX_RX` and the
  git-verb check linear. Net −77 lines before round 0's fixes. Round 0 ran: 7 findings,
  no 🔴, verdict *deletion candidate* — all addressed.
- ⚠ **`#1811` is NOT merged and NOT deployed.** The hook is a `home.file` copy: merge →
  pull → `home-manager switch`/`ship.sh`, or both hosts keep running `#1799`'s version.

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
1. **Merge `#1811`, then `scripts/ship.sh` both hosts.** Read every per-host line, then
   confirm the deployed copy has NO `GIT_VERB_SCAN_CAP` constant — `readlink -f` is the
   arbiter, never a diff.
   forcing: gate — a fleet-wide Stop hook whose deployed copy carries a cap, a truncation
   and a test that this repo has decided should not exist.
2. **Operator call: lift `test_the_hook_spawns_no_subprocess_on_any_path`?** An OBSERVATION
   from `#1092`'s body frozen into a prohibition, and wider than the `shutil` standard the
   same file uses for the same hot path. Lifting it allows `git cat-file -e <ref>:<path>`,
   making the ref exemption VERIFIED instead of shape-matched — which would have prevented
   the `handoff-x.md` false firing this arc actually hit. 🔴 Round 0 of `#1811` independently
   named this as the one requirement it would drop, and noted no round has escalated it.
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
  cap truncated the HEAD. Re-derived 2026-09-20 over all 6,626 transcripts, 21,291 match
  sites: head max **29,039 B**, p99 3,111 B, and **108 sites exceeded the cap**. The
  deletion is still right, but because the scans are now linear — never because the input
  is small. As first written the argument would have licensed deleting the cap WITHOUT the
  changes that make it safe.
- 🔴 **THE SANDBOX TIER WAS THE GAP EVERY AUDIT ROUND NAMED, AND CLOSING IT IS WHAT THE
  MERGE RESTED ON — carried here because it is EVIDENCE, and `State now` is a REPLACE
  heading that had already been warned for dropping it once.** All three rounds of
  `#1799` verified the dev-host tier only and each said so. `tekton/devrc-pytests` on
  `d7af847c`: **`collected=24037 passed=24032 skipped=4 failed=1`**, the single failure
  being the inherited opencode pin, unreachable from a diff touching only
  `scripts/claude-hooks/**`. `cairn-client-runs`, `gotests` and `nodetests` all success.
  Waiting ~20 minutes for that verdict is the only reason the merge was not blind.

## How to verify
```bash
# the defect and the fix, against whichever copy you mean — run BEFORE and AFTER a switch
python3 - <<'PY'
import importlib.machinery, importlib.util, os
H = os.path.expanduser("~/.claude/hooks/handoff-write-guard.py")   # or the branch copy
ld = importlib.machinery.SourceFileLoader("g", H)
sp = importlib.util.spec_from_file_location("g", H, loader=ld)
m = importlib.util.module_from_spec(sp); ld.exec_module(m)
def probe(cmd):
    return m.handoff_read_docs({"tool_name": "Bash", "tool_input": {"command": cmd},
                                "cwd": os.path.expanduser("~/workspace/devrc")})
print("absent doc, plain    ->", probe('grep -rn "claudedocs/handoff-no-such-doc.md" .'))
print("real doc             ->", probe('cat claudedocs/handoff-guard-existence-gate.md'))
print("absent doc, off a ref->", probe('git -C ~/workspace/devrc show HEAD:claudedocs/handoff-no-such-doc.md'))
PY
# AFTER the fix: [] · [<path>] · [<path>]
# 🔴 The THIRD line still arms by design — that is the declared residual over-match, and it
#    is the one that actually fired on this session. Rank 3 above is the decision about it.

# the deployed copy is the new one — readlink is the arbiter, never a diff
grep -c _read_off_a_ref "$(readlink -f ~/.claude/hooks/handoff-write-guard.py)"

nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-guard-exist/scripts/claude-hooks/tests/test_handoff_write_guard.py -q
```
