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
- Branch `fix/handoff-guard-existence-gate`, commit `0ac60ad1`, based on `cee56910`.
  **PR `#1799` OPEN**, `mergeable=MERGEABLE`, `mergeStateStatus=UNSTABLE`, all four
  Tekton checks `PENDING` at hand-off time.
- **Files changed (2):** `scripts/claude-hooks/handoff-write-guard.py` and
  `scripts/claude-hooks/tests/test_handoff_write_guard.py`. +250/−24.
- **DONE:** `_resolve` gates on the FILE (`os.path.isfile`) instead of only the
  resolved `claudedocs/` DIRECTORY; the `git show <ref>:claudedocs/<doc>` case is
  preserved explicitly by a new `_read_off_a_ref`; the Read arm now requires the
  literal `claudedocs/` segment, which the Bash arm (`HANDOFF_PATH_RX`) and
  `is_handoff_write` always did.
- **NOT AUDITED.** `/audit-pr 1799` has not been run — neither round 0 nor the nine
  correctness axes. **NOT MERGED, NOT DEPLOYED.**
- 🔴 **DEPLOYED ≠ MERGED HERE, AND THE HOOK IS THE `home.file` KIND.**
  `~/.claude/hooks/handoff-write-guard.py` is a nix-store COPY, so a `git pull` changes
  nothing: the sequence is merge → pull → `home-manager switch`/`ship.sh`. Until then
  the guard on both hosts is the OLD one and will keep firing on fixtures.
- **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5** — 0 tasks
  for this session, with its positive control confirming the board answered. A wrong id
  also answers 200/empty, so that zero is a real reading and NOT a clean bill of health.

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

## Next steps (ranked)
1. **`/audit-pr 1799` — round 0 FIRST, then the nine correctness axes.** Round 0 is the
   only round that can conclude *close this PR*, and that is actionable only while the
   merge decision is open; it reports and cannot end a ladder.
   forcing: gate — an unaudited PR changing a fleet-wide Stop hook that can BLOCK a
   turn on every session on both hosts.
2. **Merge `#1799`, then `scripts/ship.sh` both hosts, then re-run the probe below.**
   🔴 Read every per-host line of `ship.sh`, not the final verdict, and confirm the
   DEPLOYED copy carries `_read_off_a_ref` — `readlink -f` is the arbiter, never a diff.
   forcing: gate — merged ≠ deployed; this hook is a `home.file` copy, so the fix is
   inert on both hosts until a switch runs.
3. **Merge `#1780`** (`docs/handoff-arc-closed-final`, touches only
   `claudedocs/handoff-find-session-arc-resolution.md`). Until it lands, `origin/main`
   carries that arc's PRE-CLOSE copy — a 4-item ranked list for a finished arc — so
   every `/resume` re-opens an arc whose closing condition is met.
   forcing: gate — a stale queue on `main` that re-opens a closed arc on every read.
4. **Re-key the three `v1.18.29` claims to the installed opencode**, per the open
   investigation above. Do NOT loosen the assertion.
   forcing: regression — `main-green-check` rc 10, RED and REPRODUCED on `52939157`.

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

## How to verify
```bash
# 1. the defect, and that the fix addresses it — run against the DEPLOYED hook after a
#    switch, and against the branch copy before one. `armed` must be [] for a doc that
#    does not exist and non-empty for one that does.
python3 - <<'PY'
import importlib.machinery, importlib.util, os
H = os.path.expanduser("~/.claude/hooks/handoff-write-guard.py")   # or the branch copy
ld = importlib.machinery.SourceFileLoader("g", H)
sp = importlib.util.spec_from_file_location("g", H, loader=ld)
m = importlib.util.module_from_spec(sp); ld.exec_module(m)
def probe(cmd):
    return m.handoff_read_docs({"tool_name": "Bash", "tool_input": {"command": cmd},
                                "cwd": os.path.expanduser("~/workspace/devrc")})
print("absent doc  ->", probe('grep -rn "claudedocs/handoff-no-such-doc.md" .'))
print("real doc    ->", probe('cat claudedocs/handoff-find-session-arc-resolution.md'))
print("off a ref   ->", probe('git -C ~/workspace/devrc show HEAD:claudedocs/handoff-no-such-doc.md'))
PY
# EXPECTED after the fix: absent=[] · real=[<a path>] · off-a-ref=[<a path>]
# BEFORE the fix all three return a path — that is the bug.

# 2. the test matrix, on the branch
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-guard-exist/scripts/claude-hooks/tests/test_handoff_write_guard.py -q

# 3. the deployed copy is the new one (readlink is the arbiter, never a diff)
readlink -f ~/.claude/hooks/handoff-write-guard.py
grep -c _read_off_a_ref "$(readlink -f ~/.claude/hooks/handoff-write-guard.py)"
```
