# Handoff: skill-usage-telemetry — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make Claude Code **skill usage** measurable, because it was not: `adoption-scan` sees only
the 9 tools that emit through `invocation.py`, and skills emit nothing. An investigation
asked *"was the signal skill ever used operationally?"*, searched keywords, and answered
**"never"** — the reverse of the truth. This work ships the measurement AND removes the
doc claim that made the wrong answer look right.

## State now
- **Branch:** `feat/skill-usage-telemetry`, pushed, clean tree. **PR #1000** OPEN.
- **Base:** contains `origin/main` as of `6e7e85bf` (merged in at `d382f1e9`).
- **Gate:** `GATE: RESULT=PASS exit=0`, both tiers, at `bd869dc5` —
  `TOTAL collected=18651 passed=18649 skipped=2 failed=0`. Tekton checks were still
  `pending` at hand-off; nothing else blocks.

**DONE this session**
- `scripts/collector/claude/session-tailer.py` — Layer A rollup now carries
  `skills_used` / `skills_invoked` / `commands_typed` / `unusable_skill_names`, from three
  independent routes (`attributionSkill`, a `Skill` tool_use's `input.skill`, a typed
  `<command-name>`). Identities bounded by `canonical_skill_name`; rejects counted.
- `scripts/lib/transcript_search.py` — `--skill` predicate (`session_used_skill`),
  `canonical_skill_name`, and **`search_peers()`**: the OTHER hosts' Claude corpora over
  SSH (`bd869dc5`).
- `scripts/find-session.py` — `--skill NAME`, integrated with #989's `--live` machinery.
- `claudedocs/proposal-skill-usage-telemetry.md` — the gap analysis (G1–G5 / P1–P5).
- `claudedocs/followups-skill-usage-telemetry.md` — **read this**; it is the outstanding-work
  list, each item with a closing condition.
- `scripts/run-tests.sh` — `scripts/collector/claude/tests` floor 110 → **164**, the number
  the gate printed on the merged tree (`51aac4b7`).

**Verification posture**
Five blind audit rounds (~50 findings), every guard mutation-tested, gated on the **merged**
tree not the branch. 🔴 **Zero findings were in the shipped mechanism** — all were in
guards, tests, measurements or sentences.

**NOT deployed.** `skills_used` is **forward-only**: no row carries it until PR #1000 merges
AND `scripts/ship.sh` runs (the tailer restarts on switch; rows appear on the next 5-min tick).

## Open investigations — live diagnosis state
### Does `--skill` reach the laptop? (blocked on ship, not on code)
- **Symptom + exact repro:** `find-session.py --skill signal` on the workbench.
- **Observed (verbatim):**
  `find-session: peer laptop (10.42.0.100): peer is running an older transcript_search with no --skill support (run ship.sh) — its Claude sessions are NOT in these results`
- **Ruled out:** the peer leg being broken. **Positive control:** the workbench holds
  **zero** vetr sessions, and
  `find-session.py "qa-coverage-and-device-access" --claude-only` returns
  `1. [2026-08-28 22:09] vetr (main) [claude-remote] · 6 hits` +
  `2. [2026-08-29 00:49] vetr (main) [claude-remote] · 3 hits` — both from the laptop.
  So SSH, discovery, merge and tagging all work; only the capability probe is refusing.
- **Leading hypothesis:** working as designed. The probe (`hasattr(ts, "canonical_skill_name")`)
  refuses rather than searching WITHOUT the filter, which would return every term match as
  though it used the skill.
- **Next probe:** after merge, `scripts/ship.sh`, then re-run
  `find-session.py --skill signal --limit 20` and expect the laptop's 5 signal sessions.

## Next steps (ranked)
1. **Merge PR #1000**, then run `scripts/ship.sh`. Nothing downstream can be verified before
   this — every other item is gated on rows existing. Repo: `devrc`.
2. **Verify the emitter live.** After ship + one 5-min tick:
   ```sql
   SELECT count() FROM activity.events
   WHERE source='claude' AND kind='session-summary'
     AND JSONLength(payload, 'skills_used') > 0
   ```
   Non-zero is the precondition items 3 and 4 wait on. Also re-run item 1's `--skill` probe
   above.
3. **Add the `adoption-scan` `via: "skill"` registry arm.** Gated on item 2 returning
   non-zero — adding it earlier reports every skill as `DEAD`.
   Files: `scripts/session-analysis/adoption-scan.py`, `claude/skills/adoption-scan/SKILL.md`.
4. **Add the `attributionSkill` deadman.** Same gate as item 3; earlier it is a permanently-red
   gate. File: `scripts/validation/invariants.py`.
5. **Work `claudedocs/followups-skill-usage-telemetry.md`** — 4 items, each with a closing
   condition: `audit-dispatch.py`'s wrong-toolchain brief, a credential rotation, G4 routing
   (`adoption-scan` + `activity` never mention `--skill`), G5 the ClickHouse creds/query helper.
6. **Release the claim** when 1–2 are done: `claim-work --release skill-usage-telemetry`.

## Gotchas / decisions / dead-ends
- 🔴 **`find-session`'s "both hosts" claim was HALF FALSE for weeks** and is the root cause of
  the original wrong answer. `opencode_search` went cross-host 2026-08-26; `transcript_search`
  never did. Fixed in `bd869dc5` by adding the leg, **not** by weakening the sentence.
- 🔴 **A guard measured only by what it REJECTS is not measured.** Twice: reverting one
  argparse default (`--skill default=None` → `""`) made the guard fire on every plain keyword
  search — `find-session signal` exit 2, zero output, tool's primary mode dead — with the
  **whole CLI suite green**. Negative controls added both times.
- 🔴 **A retraction in a commit message is not a retraction.** I "retracted" a wrong figure in
  a commit while three code sites still asserted it; the delta audit caught it. Re-measured, it
  was wrong about the **corpus** too (the shape lives only in `subagents/`, which neither
  reader walks).
- **Three routes, not two.** A `Skill` tool_use carrying `input.skill` was missed initially —
  undercounted `next-lever` by 87.5% (1 of 8). `input.args` beside it is operator free-text and
  is never kept.
- **The bound is duplicated on purpose**, pinned byte-identical by a test: `nix/home.nix`
  deploys `scripts/collector/claude` **alone** to the daemon's runtime path, so an import from
  `scripts/lib` would pass every test and break the running service on both hosts.
- **Path-derived prefixes are dropped** (`<path>:<skill>` → `<skill>`) to stop a per-run-unique
  filesystem path becoming an unbounded ClickHouse map key in a PUBLIC repo. Forward-looking:
  **0** namespace-qualified identities in the 837 session transcripts either reader walks.
- **`--skill` forces the archive leg.** #989's `--live` short-circuits the archive when it
  matches; without forcing, the skill filter would never run and live rows chosen on TERMS
  ALONE would print under a heading read as a skill answer. A clean `git merge` cannot see that.
- **`--skill` is exact on the CANONICAL form**, so `apps/api:deploy` and `apps/web:deploy` both
  match `deploy`. Deliberate; the identity measured is the skill, not where it loaded from.

## How to verify
```bash
# 1. the question that started this — 666 keyword hits vs 1 real use
python3 ~/workspace/devrc/scripts/find-session.py signal --claude-only | head -1
python3 ~/workspace/devrc/scripts/find-session.py --skill signal --limit 20

# 2. the cross-host leg, with its positive control (workbench has NO vetr sessions)
python3 ~/workspace/devrc/scripts/find-session.py "qa-coverage-and-device-access" --claude-only
#    expect rows tagged [claude-remote]

# 3. the gate, both tiers — the sandbox tier is what the merge gates on
nix develop ~/workspace/devrc --command bash ~/workspace/devrc/scripts/gate.sh --tier both --set all
nix build ~/workspace/devrc#checks.x86_64-linux.pytests
```
