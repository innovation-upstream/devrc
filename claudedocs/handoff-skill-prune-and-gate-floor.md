# Handoff: skill-prune-and-gate-floor — 2026-08-19

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal

Two arcs, one session. (1) Run the prune pass the browser-per-site handoff had queued —
`scripts/browser-bridge/SKILL.md` was 3 bytes under its enforced budget. (2) The prune
surfaced that the repo's hermetic gate was red for everyone; close the half that was mine.

The browser arc's own handoff lives in the CLIENT repo:
`<datapacket-talos>/claudedocs/handoff-browser-per-site-reference.md` (updated this
session, `c14952698`). This doc is the devrc-side record and does not repeat it.

## State now

🔴 **All three PRs MERGED and live. The hermetic gate is GREEN. Nothing is in flight.**

| ref | what |
|---|---|
| **#566** `7c98ec0` | browser `SKILL.md` prune + 4 correctness defects |
| **#571** `add4f77` | the accounting comment for the floor re-pin #570 landed bare |
| **#576** `27ae90e` | corrected the `#551` warning #571 had landed, already false an hour later |

**Gate:** `nix build .#checks.x86_64-linux.pytests` → `RESULT: PASS (exit=0)`,
`TOTAL collected=12704 passed=12703 skipped=1 failed=0`. Both reasons it had been red are
closed — the opencode pin by **#570** (not me) and the browser-bridge drift ceiling by
**#570 + #571**.

**Deployed and verified as the CONSUMER, not just the deploy:** `SKILL.md` is an
`mkOutOfStoreSymlink`, so merge + `git -C ~/workspace/devrc merge --ff-only origin/main`
makes it live with **no `home-manager switch`**. `readlink -f ~/.claude/skills/browser/SKILL.md`
terminates in the clone and serves the new body.

🔴 **Re-measure the size before quoting it — this file moved TWICE in one evening.**
11,942 B after #566, then **11,990 B / headroom 298** after #551 (`f199f23`) spent 48 B
making the `activate` i3 raise opt-in. Usable margin (headroom minus the 250 floor) is
now **48 B**.

## Open investigations — live diagnosis state

### The `kubectl exec` ask-gate — the ONE open decision, untouched this session

- **Live state, re-derived at 2026-08-19 (do not trust this line, re-run it):**
  `grep -c 'kubectl.*exec' scripts/opencode/opencode.jsonc` → **1**;
  same grep on `$(readlink -f ~/.config/opencode/opencode.jsonc)` → **1**.
  **Repo and live agree WITH the gate.** No production-safety exposure.
- **The vanished edit is NOT lost.** The browser-per-site handoff records "no stash, no
  dangling commit found". It is on branch **`wip/opencode-kubectl-exec-allow`** at
  `62f0539` ("wip(opencode): preserve an uncommitted kubectl-exec permission downgrade"),
  and the diff is exactly the one line the handoff recovered:
  `- "*kubectl*exec*": "ask",`
- **What removing it actually costs** (from the other handoff, not re-derived here): four
  safety assertions fire, including `agent=k8s` resolving `allow`, and
  `test_dangerous_command_family_is_gated`. Landing it means deleting `kubectl exec` from
  the dangerous-command fixture — silencing the test whose purpose is that assertion.
- **Next probe / decision:** if it is genuinely to be removed it must handle the **`k8s`
  agent path** and the **dangerous-family assertion** rather than deleting the assertion.
  Its own reviewed change. Otherwise nothing to do.

## Next steps (ranked)

1. **Nothing is required.** All three PRs merged, gate green, everything verified live.
2. **Decide the `kubectl exec` gate** (above) — the only real open item, and it predates
   this session.
3. **Do NOT re-run a prune on `browser/SKILL.md` expecting more room.** See the caveat
   under Gotchas — the answer will be the same and the reason is structural.
4. **Four nits #566 deliberately skipped**, each confirmed by a delta audit as not made
   worse: `whitespace-normalized` survives only in `README.md`; the `screenshot` ops row is
   the one edited row with no `reference/` pointer and lost its 133K–890K token-cost
   deterrent; the `ONE text --wake` escalation remedy has no destination in `reference/`;
   per-row paths use the "expansion stated once" form rather than the size gate's own
   "repo-absolute per row" playbook step.

## Gotchas / decisions / dead-ends

- 🔴 **`scripts/skill-audit.py` gave a FALSE ALL-CLEAR on the only file that mattered.** It
  reported `✓ OK — no prune needed` while the file sat 3 B under its enforced budget: the
  auditor measures against the raw `MAX_BYTES` ceiling and **cannot see
  `MIN_HEADROOM_BYTES`**, so it misses the real budget by 250 B. Read the numbers from
  `scripts/browser-bridge/tests/test_skill_size.py`, which owns them.
- 🔴 **The body is DENSE, not bloated — a prune cannot buy meaningful room here.** Measured:
  every section sits at or below its own historical density (Ops 190 B/row against a
  measured 190 mean; Reference 164 against 166) and the core routes ~11.6× its own weight.
  The prune reclaimed 663 B; the correctness defects it surfaced cost ~570 B. **More room
  means raising `MAX_BYTES` deliberately or demoting a 🔴 trap — not another shave.**
- 🔴 **A staleness pass that checks TOKEN PRESENCE cannot test a CLAIM.** #566's pass
  verified 24/24 ops, 16/16 fields, 13/13 error strings — and still shipped a row asserting
  `extension_stale` is on `health` ONLY. `server.py:2883` (`_whoami`) and `:2948` (health)
  both call `annotate_staleness`; `/instances` (`:2990`) does not. The field NAME existed
  everywhere the pass looked. Only reading the claim against the server found it.
- 🔴 **"Fix EVERY copy" failed in the exact way its own documentation warns about.** #566's
  focus/workspace fix landed in the body while `reference/spa-wake.md` and
  `reference/x-fallback.md` — both files the body ROUTES TO for `activate` — kept the
  pre-fix "restore focus" text. I had grepped both, seen them, and read them as proof the
  command *survived* rather than as two stale copies.
- 🔴 **Each audit round's fixes created the next round's findings — three rounds.** r1 five
  findings; r2 caught that r1's health-row fix implied `/instances` carries the field and
  that r1's justification INVERTED the comment it cited; r3 caught that "focus alone leaves
  them on YOUR workspace" contradicted a mechanism added one clause later. **The instruction
  was right every time; only the REASON was wrong** — and a wrong reason beside a safety
  guard is what talks the next maintainer into deleting the guard.
- 🔴 **A record stopped being true three times WHILE I worked from it.** The handoff's byte
  count; my own `#551` warning (merged, then #551 merged an hour later); and `11,942`, which
  I nearly wrote into a doc 40 minutes after measuring it — real value `11,990`. Every one
  was caught by re-measuring **at the moment of acting**, never at the moment of planning.
- 🔴 **The drift ceiling is DERIVED — there is no ceiling constant to edit.**
  `drift = max(60, floor/4)`, `ceiling = floor + drift` (`run-tests.sh:1566-1568`). A
  ceiling failure means the FLOOR has gone stale, and the fix is to raise the floor to
  `_suggested_floor` (`:1184`) applied to the gate's own printed count — **the failing run
  prints the replacement itself**. I proposed "raise the ceiling 586 → 654"; both halves
  were wrong (no such constant; the right value is 622).
- **A stale floor is an ABSENT guard that still prints a number.** At floor 469 against 654
  collected, a collapse of a QUARTER of the browser-bridge suite would have fitted
  underneath and reported green. The re-pin cut the silent band 185 → 32.
- 🔴 **Two `nix build` runs reported exit 0 while their CONTENT said `RESULT: FAIL`** — the
  trailing `tail` swallows the status. Read the RESULT line, never the exit code.
- 🔴 **Run the discriminating control BEFORE the plausible theory.** Three times a failure
  looked like mine and was not: both gate failures reproduced identically on unmodified
  `origin/main`; and 11 local test failures in the floor-table tests were byte-identical on
  `main` (failure sets diffed) — a nix-env artifact.
- **`gh pr view … mergeable` can be STALE right after a push.** It read `CONFLICTING` on a
  branch that was literally `main` plus one commit; local `git merge-tree` exit 0 and a
  re-poll settling to `MERGEABLE/CLEAN` is what resolved it.
- **The bash-guard cannot resolve `-C "$WT"` when `$WT` came from a file** — it judges the
  caller's cwd instead and blocks the commit as "on trunk". Pass `-C` a literal absolute
  path.

## How to verify

**The gate (authoritative; devrc has NO CI — `gh pr checks` reporting "no checks" is by
design):**
```bash
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests 2>&1 | tail -30
# read the RESULT line and the per-target rows, NEVER the exit code
# expect: RESULT: PASS (exit=0), and PASS scripts/browser-bridge/tests (... floor=622)
```

**The skill body, at the DEPLOYED path (a push is not a saving):**
```bash
wc -c "$(readlink -f ~/.claude/skills/browser/SKILL.md)"   # expect 11990; re-measure, it moves
cd ~/workspace/devrc && python3 -m pytest scripts/browser-bridge/tests/ -q   # expect 654 passed
```

**The size gate can go red** (negative control — a gate never watched failing is a claim):
```bash
SC=/tmp/negctl-$$; cp -a ~/workspace/devrc/scripts/browser-bridge "$SC"
python3 -c "open('$SC/SKILL.md','a').write('x'*400)"
python3 -m pytest "$SC/tests/test_skill_size.py" -q   # expect FAIL naming RECLAIM
rm -rf "$SC"
```

**The floor guard, both directions:**
```bash
cd ~/workspace/devrc && bash scripts/run-tests.sh --check-floors   # expect RESULT: PASS, floor 622
```
