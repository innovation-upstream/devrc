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
- **PR `#1799`, head `d7af847c` — FOUR commits.** `0ac60ad1` the fix · `f7ccbfce` round 0 ·
  `be12c7ae` round 1 · `1a2943ad` round 2 · `d7af847c` round 3. Based on `cee56910`.
- ✅ **THE AUDIT LADDER RAN: rounds 0, 1, 2 AND 3.** Every round found things that needed
  fixing, so none of them was a stopping round. Round 3's advisory verdict is **safe to
  merge**; its executable delta is **behaviour-neutral across 17,978 real corpus commands**.
  Claims blocks posted for rounds 1 and 2 (`payload=94`, `payload=100`) — both non-zero, so
  the attribution gate never fired and the ladder continued by the rule, not by inertia.
- **PR body REWRITTEN** to match the tree (it advertised the Read-arm narrowing round 0
  removed). This repo squash-merges, so that body becomes `main`'s permanent commit message.
- 🔴 **NOT MERGED, NOT DEPLOYED.** `~/.claude/hooks/handoff-write-guard.py` is a nix-store
  COPY: merge → pull → `home-manager switch`/`ship.sh`. Until then both hosts run the OLD guard.
- ⚠ **ONE MUTATION RESULT IS UNRESOLVED AND IS NOT ROUNDED UP.** `test_the_scan_cap_BOUNDS_
  the_search` kills `head = cmd[:start]` (cap deleted) — observed, own assertion. It does NOT
  have an established verdict for `GIT_VERB_SCAN_CAP = 10**9` (cap inert): the pytest run
  carrying that mutant does not terminate, twice. That is COULD-NOT-MEASURE, neither pass nor
  kill. See the open investigation below.
- **`main` moved a lot during this work** (`cee56910` → `a2b1893a` → `c46bb9d4` → `7ef01c05`
  → …). `#1780` was merged this session (`a2b1893a`), so `main` now carries the
  find-session arc's CLOSED state.

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

## Next steps (ranked)
1. **Decide the ladder's end and merge `#1799`.** Round 3 is advisory safe-to-merge and every
   open item is a sentence, not behaviour. Merging means `gh pr merge --squash`, then
   `scripts/ship.sh` BOTH hosts, then re-run the probe in *How to verify* against the
   DEPLOYED copy — `readlink -f` is the arbiter.
   forcing: gate — a fleet-wide Stop hook that can block a turn, sitting unmerged with a
   4-commit audit ladder already paid for.
2. **Settle the unresolved mutant** per the open investigation above — one narrow, terminating
   question, not a general round 4.
   forcing: gate — the ladder's only surviving guard has an unestablished mutation verdict,
   and "could not measure" was recorded rather than rounded to a pass.
3. **Operator call: lift `test_the_hook_spawns_no_subprocess_on_any_path`?** It is an
   OBSERVATION from `#1092`'s body frozen into a prohibition, wider than the `shutil` standard
   the same file uses for the same hot path. Lifting it allows `git cat-file -e <ref>:<path>`,
   which would make the exemption VERIFIED instead of shape-matched — and would have prevented
   the false firing this session actually hit (`handoff-x.md`, armed via a `<ref>:` prefix in a
   measurement command, a doc that has never existed).
   forcing: user — reopens the fix shape the operator originally chose; not mine to take.
4. **Re-key the three `v1.18.29` claims** — `main` is RED on `test_opencode_engine.py:745`.
   🔴 The dual-binary control is AVAILABLE NOW and DECAYS: `1.18.29` is still realised at
   `/nix/store/6pw7n475…`. After a GC it is not, and the strong control is gone. Both hosts are
   on `1.18.30` at the same store path; the cheap control already passes (`1 failed, 24 passed`,
   the failure being exactly the version assertion — identical to both prior re-derivations).
   forcing: regression — `main-green-check` rc 10, RED and REPRODUCED.

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
