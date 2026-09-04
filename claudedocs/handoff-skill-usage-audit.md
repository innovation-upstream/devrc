# Handoff: skill-usage-audit — 2026-09-04

## Run this first — the index, one command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Audit which of the skills in context are actually used, using the ClickHouse
session-summary telemetry, and recommend tier changes or retirements.

## State now
- Branch: `docs/skill-usage-audit-subsystem-index-tier` off `origin/main` @ `5793c0e5`
- Rank 1 is CLOSED (below). Rank 2 (`deadman.py`) is untouched.
- Claim: `skill-usage-audit-1` — release with
  `claim-work --release skill-usage-audit-1` when this lands.

---

## 🔴 READ THIS BEFORE ANY NUMBER BELOW — the 2026-09-04 figures were wrong three ways
The first pass read ONE field over a window it did not have, and summed rows it took
for sessions. Every headline count in that pass is inflated ~3x and describes 6 days,
not 30. Corrected by re-measurement on 2026-09-04:

1. **`skills_used` is not a use count.** Per the tailer's own header
   (`scripts/collector/claude/session-tailer.py:411`) it is
   `{skill: assistant-records attributed to it}`, read off Claude Code's
   `attributionSkill`. One typed command produces many records — a measured example
   row: `commands_typed {manage-mongo: 1}` beside `skills_used {manage-mongo: 10}`.
   So "19,211 uses" was never a count of uses.
2. **8,406 was ROWS, not sessions.** The tailer re-emits a growing summary as a session
   progresses: 2,182 observable rows resolve to **302 distinct `session` values**
   (~7 emissions each). Any query that `sum()`s across rows triple-counts. Aggregate
   `max()` per `session` first.
   🔴 **The per-session summary is NOT monotonic** — session `74dd2866`'s LAST row
   carried `skills_invoked {}` after an earlier row carried
   `{"subsystem-index":1,"audit-pr":1}`. So `max()` over the session's rows is the
   defensible aggregation; `argMax(payload, ts)` (latest row) silently undercounts.
3. **The 30-day window does not exist.** These fields only appear in **2,182 of 8,545**
   session-summary rows. Presence by day: first rows 2026-08-17, sporadic through
   08-28, then **every row from 2026-08-30 onward**. The full-coverage window is
   **2026-08-30 → 09-04, six days**. Rows before 08-17 carry no `skills_used` key at
   all, so a `!='{}'` filter reads them as "no skills" rather than "not observed".
   ✅ Good news: whenever `skills_used` is present, `skills_invoked` and
   `commands_typed` are present too — identical per-day counts — so there is no
   field-presence confound BETWEEN the three signals.
4. **Two of the three signals were never read.** `skills_used` alone cannot tell an
   auto-fire from an explicit call. The other two can, and were sitting in the same
   payload — see the roster below.

---

## Rank 1 — RESOLVED: `subsystem-index`'s uses are via its CALLERS, never standalone
**Verdict: tier B is correct, and the demotion costs nothing.** Measured over the
284–302 observable sessions (window as corrected above).

| signal | subsystem-index | handoff (for contrast) |
|---|---|---|
| sessions with attributed records (`skills_used`) | 178 | 219 |
| sessions with an explicit `Skill` tool call (`skills_invoked`) | **178** | 130 |
| sessions where the operator TYPED `/name` (`commands_typed`) | **0** | 153 |

- **`attributed == invoked`, exactly (178 == 178). `subsystem-index` has never once
  auto-fired from its description** — every appearance is preceded by an explicit
  `Skill` call. Contrast `handoff`, where 219 attributed vs 130 invoked means it
  genuinely does auto-fire. Its description is doing **zero** routing work.
- **176 of 178 (98.9%) co-occur with a `handoff` signal.** The 2 exceptions
  (`d666eaf7` in `datapacket-talos`, `74dd2866` in `auditloop`) each invoked it
  **once via the Skill tool** with `commands_typed {}` — no typed command in the
  session at all. So neither is operator routing either; both are a caller.
- **0 sessions have ever typed `/subsystem-index`.**
  🔴 Positive control for that zero: the identical expression against `handoff`
  returns **153** sessions. The zero is a reading, not a wire to nothing.
- **Structural corroboration** — `claude/skills/handoff/SKILL.md:126` directs "follow
  the **`subsystem-index`** skill, whole, and come back here", and already carries an
  absolute-path fallback (`~/.claude/skills/subsystem-index/SKILL.md`) *for exactly
  the eviction case*, reasoning that its own description says "rarely run directly".
  It is also named in the bodies of `prune-index`, `cairn` and `analyze-service`.
- 🔴 **The strongest part: this was measured with tiers NOT ADOPTED.**
  `~/.claude/settings.json` on the workbench has **no `skillOverrides` key** (top-level
  keys: `alwaysThinkingEnabled autoCompactWindow enabledPlugins fileCheckpointingEnabled
  hooks permissions preferredNotifChannel skipWorkflowUsageWarning statusLine theme
  voiceEnabled`) — which is the NOT-ADOPTED state `drift-check.sh` rc 22 reports and
  deliberately does not count as drift. So `subsystem-index`'s **full** description was
  live for the entire window and still routed nobody. Demoting it to name-only removes
  routing surface that is measured to carry no traffic.

**No tier change needed. The ledger's stated reason — "invoked BY `/handoff`, which
names it in its own body" — is now measured true rather than assumed.**

## Corrected roster — all three signals, deduped by session
Sessions touching each skill (not record counts). `claude/skills/` roster only, so the
two `mkOutOfStoreSymlink` skills (`browser`, `dl-router`) are absent from this table.

| skill | tier | sess attr | sess Skill | sess typed |
|---|---|---|---|---|
| handoff | A | 219 | 130 | 153 |
| subsystem-index | B | 178 | 178 | 0 |
| audit-pr | A | 139 | 139 | 0 |
| resume | A | 85 | 80 | 5 |
| clawgate | A | 23 | 20 | 3 |
| clickup | A | 8 | 5 | 3 |
| tekton | A | 8 | 8 | 0 |
| obs-read | A | 7 | 7 | 0 |
| signal | A | 7 | 7 | 0 |
| analyze-service | A | 6 | 1 | 5 |
| activity | A | 3 | 3 | 0 |
| mailbox | A | 3 | 2 | 1 |
| prune-skill | B | 2 | 2 | 0 |
| cairn / i3 / prune-index / session-manager / verify-agent | B/A | 1 each | 1 each | 0 |

**19 devrc skills have NO SIGNAL on any of the three fields** in the window:
`adoption-scan auditloop bar check-clickup-addressed close-the-loop devrc-dx
espanso-audit find-session hetzner initiative-scan initiatives prune-memory
quiesce-workload repo-cos sglang standup ux-audit-loops vetr-mailbox window-triage`.

🔴 **This partially REFUTES the first pass's "extractor blind spot" conclusion.** Those
skills were not missed by a blind extractor — reading all three signals still finds
nothing, so in this 6-day window they were genuinely not invoked. The 12–2,087 "text
mentions" that pass counted are prose *about* the skills (this very doc contributes
some), not invocations. ⚠ Stated at measured scope: 6 days, 284 sessions, two hosts.
That is far too short to call any of them dead — `repo-cos` is weekly and `espanso-audit`
is on-demand. It is enough to say the text-mention proxy does not survive contact with
the invocation signals.

## Open investigations — live diagnosis state
- Why is the per-session rollup non-monotonic (item 2 above)? One measured instance.
  Not chased. It matters only to whoever writes the next query — use `max()`.

## Next steps (ranked)
1. Re-run `deadman.py` to confirm source roster is current
   forcing: none
2. Re-run this roster after ~30 days of full field coverage (i.e. on/after 2026-09-29)
   before drawing any retirement conclusion about the 19 no-signal skills
   forcing: the window; nothing to do until then
   closing condition: a roster run over a >=30d full-coverage window, read by whoever
   picks up this doc

## Gotchas / decisions / dead-ends
- ❌ RETRACTED from the first pass: "the `skills_used` extractor is blind to skills
  loaded via the skill tool" — it is not. `skills_invoked` records exactly that, in the
  same payload. The gap was in the query, not the extractor.
- ❌ RETRACTED: "verify-agent is the only untracked skill that also appears in
  `skills_used`". `verify-agent` appears in all three signals (1 session).
- The three fields are deliberately not merged — see the tailer's own header at
  `session-tailer.py:411-438`, which measures that none is a superset of the others
  and warns against "simplifying" to one.
- `payload` has no `session_id` field. Session identity is the **`session` COLUMN** on
  `activity.events`. `uniqExact(JSONExtractString(payload,'session_id'))` returns 1 for
  the whole table — an empty-string artifact, not one session.

## How to verify
```bash
CH=http://192.168.50.94:30123
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' ~/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml)

# 1. FIRST: how much of the window actually carries the fields (never assume 30d)
curl -s --user "activity_reader:$RPW" --data-binary "
SELECT toDate(ts) d, count() rows, countIf(JSONHas(payload,'skills_used')) has_used
FROM activity.events WHERE source='claude' AND kind='session-summary'
  AND ts>=now()-INTERVAL 30 DAY GROUP BY d ORDER BY d FORMAT TSV" "$CH/"

# 2. The rank-1 answer: attributed vs invoked vs typed, deduped by session
curl -s --user "activity_reader:$RPW" --data-binary "
WITH ps AS (
 SELECT session,
  max(JSONExtractInt(payload,'skills_used','subsystem-index'))     si_u,
  max(JSONExtractInt(payload,'skills_invoked','subsystem-index'))  si_i,
  max(JSONExtractInt(payload,'commands_typed','subsystem-index'))  si_c,
  max(JSONExtractInt(payload,'skills_used','handoff'))    ho_u,
  max(JSONExtractInt(payload,'skills_invoked','handoff')) ho_i,
  max(JSONExtractInt(payload,'commands_typed','handoff')) ho_c
 FROM activity.events WHERE source='claude' AND kind='session-summary'
  AND ts>=now()-INTERVAL 30 DAY AND JSONHas(payload,'skills_used') AND session!=''
 GROUP BY session)
SELECT count() sessions, countIf(si_u>0) si_attr, countIf(si_i>0) si_skilltool,
 countIf(si_c>0) si_typed,
 countIf(si_u>0 AND (ho_u>0 OR ho_i>0 OR ho_c>0)) si_WITH_handoff,
 countIf(si_u>0 AND ho_u=0 AND ho_i=0 AND ho_c=0) si_WITHOUT_handoff,
 countIf(ho_c>0) ho_typed_POSITIVE_CONTROL
FROM ps FORMAT Vertical" "$CH/"

# 3. Is the tier ledger applied on this host? (absent key == NOT ADOPTED, not drift)
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.claude/settings.json'))); \
print('skillOverrides:', d.get('skillOverrides') is not None)"
```
