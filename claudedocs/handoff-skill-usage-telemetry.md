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

## State now — 🟢 MERGED, SHIPPED, AND VERIFIED LIVE (2026-08-29)
- **PR #1000 MERGED**, squash `538370f5`, 21:28:25Z. Verified **by content** on
  `origin/main` (a squash never makes the head an ancestor, so `--is-ancestor` would have
  said "not merged" here and been wrong).
- **Gated on the MERGED tree, not the branch** — `main` had moved **22 commits** past the
  PR's base (`6e7e85bf` → `68ea76c6`) and both sides edit `scripts/run-tests.sh`. Built an
  integration branch off current `main`, merged #1000 into it, ran BOTH tiers there:
  - sandbox (`nix build .#checks.*`, the tier Tekton gates on): pytests
    `collected=18683 passed=18681 failed=0`, nodetests `tests=1366 pass=1366 fail=0`,
    both `RESULT: PASS`. Both derivations were genuinely **built**, not substituted —
    the log says `these 2 derivations will be built`.
  - dev-host (`gate.sh --tier both --set all`): `GATE: RESULT=PASS exit=0`,
    `collected=18690` across **29** targets (one more than the sandbox's 28).
  - The contested floor held: `scripts/collector/claude/tests collected=172 floor=164`,
    **no drift-ceiling trip**. The `164` was computed against `07890ebc`; it is still
    correct against today's `main`. Nothing needed re-pinning.
- **SHIPPED.** `scripts/ship.sh` converged **both** hosts to `538370f5` and actually
  **compared** them (`2 hosts compared, both at 538370f5`) — not the one-host
  `NOT COMPARED` case. Workbench `562 links checked, 0 dangling / 393 repo-sourced,
  0 stale`; laptop `508 / 0` and `378 / 0`, fast-forwarded from `638959b4` (it was behind).
  Workbench tree DIRTY but all 4 dirty paths untracked and **none nix-read**, so what
  deployed IS `origin/main`.

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

**DEPLOYED AND EMITTING.** The consumer was verified, not just the deploy:
`readlink -f ~/.config/activity-collector/claude/session-tailer.py` →
`/nix/store/…-hm_claude/session-tailer.py`, `cmp` **byte-identical** to `origin/main`, and
it carries all three routes. `claude-activity-source` reads `inactive/dead` — **correct**,
it is a timer-driven oneshot, not a fault.

## Open investigations — live diagnosis state
### ✅ CLOSED — the emitter works, and the headline claim is now MEASURED
Ranked item 2 is closed. First two post-deploy rows, with their **positive control**
(a bare zero would not have been distinguishable from a query wired to nothing):

| ingested_at (UTC) | host | `skills_used` | `skills_invoked` |
|---|---|---|---|
| 21:31:30 | workbench | `{"audit-pr":8,"handoff":7,"subsystem-index":20}` | 1 each |
| 21:41:41 | workbench | `{"resume":72}` | `{"resume":1}` |

`unusable_skill_names` = **0** on both — nothing is being silently rejected. All post-deploy
rows *have* the field (`have_field=2`), so a future empty one reads as "no skills used",
never "field absent". Control: 16 `session-summary` rows in the 2h before the deploy, **0**
with `skills_used` — the forward-only boundary is visible in the data.

🔴 **The headline, now measured rather than asserted:** `find-session signal --claude-only`
returns **678** sessions; `find-session --skill signal` returns **6**. And **5 of those 6 are
`[claude-remote]` (the laptop)** — so the originating investigation was wrong *twice over*:
it matched TEXT instead of USE, and it searched ONE host. Either defect alone produces
"never used".

Spot-checked one match rather than trusting the count — `6fb90d0d` (vetr) is a **true
positive**: `attributionSkill: ['audit-pr','clawgate','handoff','signal']` and a `Skill`
tool_use with `input.skill == 'signal'`. Worth checking because its prompt text mentions a
"signal-send-path", which is vetr's own feature and exactly the false positive this feature
exists to avoid. It matched on use, not on that string.

### ✅ CLOSED — does `--skill` reach the laptop? (was blocked on ship, not on code)
**Resolved: yes.** The refusal message below no longer appears; laptop sessions come back
tagged `[claude-remote]`. The leading hypothesis was right — the probe was working as
designed and only needed the peer to carry the new code. Original diagnosis retained below.

<details><summary>original (pre-ship) diagnosis</summary>
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

</details>

## 🔴 The one thing to read before doing items 3 and 4
**Their literal closing condition is met, and the hazard it was written to protect against
is NOT yet cleared. Do not treat the non-zero as a green light.**

The followups doc says both close when the item-2 query "returns non-zero". It returns **2**.
But those 2 rows carry **4 distinct skill identities** (`audit-pr`, `handoff`,
`subsystem-index`, `resume`) against **34** devrc-managed skills
(`ls -d claude/skills/*/ | wc -l`, measured 2026-08-29 — the scope is devrc-managed only;
plugin-provided skills are not counted). `adoption-scan` raises a loud `DEAD` at zero uses,
so adding the `via: "skill"` arm against this corpus reports **30 of 34 as DEAD** — which is
precisely the permanently-red-gate outcome the deferral existed to prevent. The deadman has
the same shape.

The gate was written as a boundary check (`> 0`) when what it actually needs is
**accumulation**. Suggested replacement, to be pinned when the arm lands — proceed when a
trailing-7d window shows a plateauing distinct-identity count on both hosts, e.g.:
```sql
SELECT host, uniqExact(arrayJoin(JSONExtractKeys(payload,'skills_used'))) AS ids, count()
FROM activity.events
WHERE source='claude' AND kind='session-summary' AND ts > now() - INTERVAL 7 DAY
GROUP BY host
```
🔴 Note `host` — at hand-off **both rows are workbench**; the laptop had not yet ticked, so
a fleet-wide claim cannot be made from this data yet. Re-check both hosts appear.

## Next steps (ranked)
1. ✅ **DONE** — PR #1000 merged (`538370f5`) + `scripts/ship.sh`, both hosts at that sha.
2. ✅ **DONE** — emitter verified live; see the closed investigation above.
3. **Add the `adoption-scan` `via: "skill"` registry arm** — 🔴 **read the section above
   first**; the stated gate is met but shipping now yields a red gate. Let identities
   accumulate, then land it.
   Files: `scripts/session-analysis/adoption-scan.py`, `claude/skills/adoption-scan/SKILL.md`.
4. **Add the `attributionSkill` deadman.** Same caveat as 3. File:
   `scripts/validation/invariants.py`.
5. **Work `claudedocs/followups-skill-usage-telemetry.md`** — 4 items, each with a closing
   condition: `audit-dispatch.py`'s wrong-toolchain brief, a credential rotation, G4 routing
   (`adoption-scan` + `activity` never mention `--skill`), G5 the ClickHouse creds/query
   helper. **These are NOT gated on data** and are the sensible next work while identities
   accumulate. G4 is the cheapest and closes the routing gap that caused the whole incident.
6. ✅ **DONE** — claim `skill-usage-telemetry-1` released.

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
