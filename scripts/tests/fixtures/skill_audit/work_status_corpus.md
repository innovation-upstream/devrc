---
name: work-status-corpus
description: SYNTHETIC fixture — a skill that really has accreted work-status narrative.
---

<!--
FIXTURE, not a real skill. Deliberately NOT named SKILL.md.

Shape being reproduced: the one-in-N skill that HAS accreted session narrative
— many `### Session <date>` blocks hanging as siblings under a single non-dated
`## Roadmap` parent. Expected classification:
  dated (work-status / EVICT_HISTORY) == the 5 `Session …` blocks
  lessons (dated but durable)         == the 1 dated-but-topical block

The two buckets must be DISJOINT here: if a Session block ever shows up under
lessons, or the durable block under dated, the split has re-merged.
-->

## Quick start

```bash
TOOL=./bin/tool
$TOOL status
```

## Failure modes (from the 2026-05-22 audit)

Durable guidance that merely cites a date. Must land in `lessons`, never in
`dated` — evicting this would gut the skill.

## Roadmap

Undated parent. The dated children below are the outermost dated blocks.

### Session 2026-07-01 — first cut

Narrative about a past working session. Belongs in a handoff doc.

### Session 2026-07-08 — wiring the collector

More narrative. Nothing here is guidance a future reader would act on.

#### Session 2026-07-09 — follow-up the next morning

NESTED on purpose. It is already inside the 2026-07-08 block, so counting it as
a block of its own double-counts its bytes and can push the projected saving
past the file size. It must NOT appear in the expected list.

### Session 2026-07-15→16 — dogfood + audit

Two-day narrative, arrow form.

### Session 2026-07-22 — fixing the retry path

Narrative.

### Session 2026-07-29 — cleanup

Narrative.

## Ops table

| command | does |
|---|---|
| `status` | print health |
