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
Audit which of the 41 skills in opencode's context are actually used, using ClickHouse
`skills_used` telemetry (last 30d), and recommend tier changes or retirements.

## State now
- Branch: `main`, behind `origin/main` by 1 commit
- Uncommitted: `nix/programs/alacritty/default.nix` (8 insertions, unrelated)
- No open PR for this work
- Clawgate: no session ID resolved (rc=3)

### Findings (last 30d, 8,406 sessions)
**20 skills tracked in `skills_used`:**
resume (41,042), handoff (29,864), audit-pr (21,242), subsystem-index (19,211),
browser (6,559), clawgate (5,867), opencode (5,633), signal (2,726), clickup (2,449),
obs-read (2,216), tekton (1,688), dl-router (1,300), analyze-service (957),
mailbox (781), prune-index (704), i3 (512), activity (202), prune-skill (151),
cairn (22), session-manager (18)

**21 skills NOT tracked but referenced in text (extractor blind spot):**
All have 12–2,087 text mentions in 30d. Heaviest: bar (2,087), auditloop (898),
verify-agent (473), initiatives (138), find-session (119). Lightest: quiesce-workload (12),
window-triage (12).

**21 skills in telemetry but NOT in context** (from other repos/projects, not relevant).

**Key insight:** No skill is truly unused. The 21 "untracked" skills are loaded via the
skill tool but `skills_used` only captures skills the extractor explicitly detects as
invoked. The existing tier assignments (20 A / 21 B) are correct — all B-tier skills
are correctly name-only (reached for by name, not by symptom).

**One flag:** `subsystem-index` has 19,211 tracked uses but is tier B ("invoked by
`/handoff`"). Verify it isn't being loaded independently for routing.

## Open investigations — live diagnosis state
(none — this was an analysis session, not a debugging session)

## Next steps (ranked)
1. Verify `subsystem-index` tier — check if 19k uses are via handoff or standalone
   forcing: none
2. Re-run `deadman.py` to confirm source roster is current
   forcing: none

## Gotchas / decisions / dead-ends
- The `skills_used` extractor is blind to skills loaded via the skill tool but not
  explicitly "used" by its definition — this is a known limitation, not a bug
- `verify-agent` is the only "untracked" skill that also appears in `skills_used` (54 uses)
- Text mentions are a rough proxy for usage; they include discussions, not just invocations

## How to verify
```bash
# Re-run the ClickHouse query
CH=http://192.168.50.94:30123
RPW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["reader-password"]' ~/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml)
curl -s --user "activity_reader:$RPW" --data-binary "
SELECT JSONExtractRaw(payload, 'skills_used') FROM activity.events
WHERE source='claude' AND kind='session-summary' AND ts>=now()-INTERVAL 30 DAY
  AND JSONExtractRaw(payload,'skills_used')!='{}' FORMAT TSV" "$CH/" | python3 -c "
import sys,json; from collections import Counter; c=Counter()
for l in sys.stdin:
    try:
        for k,v in json.loads(l.strip()).items(): c[k]+=v
    except: pass
for s,n in c.most_common(): print(f'{s:30s} {n:6d}')"
```
