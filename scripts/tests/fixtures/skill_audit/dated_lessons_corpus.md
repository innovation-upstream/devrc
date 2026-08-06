---
name: dated-lessons-corpus
description: SYNTHETIC fixture — a skill whose dated headings are durable guidance.
---

<!--
FIXTURE, not a real skill. Deliberately NOT named SKILL.md so that running
`skill-audit.py` on this repo root cannot pick it up as a skill.

Shape being reproduced: the corpus-majority skill. Every heading below either
cites an ISO date while describing DURABLE operational guidance, or carries no
date at all. NONE of them is work-status narrative. Expected classification:
  dated (work-status / EVICT_HISTORY) == []      <- must stay empty
  lessons (dated but durable)         == 9 blocks

Do not add a heading containing session / changelog / work log / shipped /
release notes / history: those words are the work-status signal and would move
a block into the other bucket, which is exactly the conflation this fixture
exists to detect.
-->

## Quick start

```bash
TOOL=./bin/tool
$TOOL status
```

## Common silent-failure modes (from the 2026-05-22 audit)

A probe that returns an empty set reports the same thing whether the query was
wrong or the subject is genuinely idle. Always pair it with a positive control.

## The inert-check defect class (2026-06-01)

A check wired to a field that no longer exists evaluates to false forever. It
never fires, and a never-firing check is indistinguishable from a healthy one.

## Why the retry budget is per-target, not global (2026-06-14)

A global budget lets one unreachable target consume every retry and starve the
rest. Measured on the staging rig: one bad target ate 96% of the budget.

## Backoff must be capped (decided 2026-06-20)

Uncapped exponential backoff pushes the next attempt past the eviction window,
so the work is dropped rather than retried.

### Sub-topic with no date

Nested under a dated parent on purpose — the outermost-only rule means this
must not be counted as a block of its own.

## Draining a queue safely (2026-06-28)

Stop the producer first. Draining while a producer is live never terminates.

## Reading the saturation panel (2026-07-03)

The panel averages over 5 minutes, so a 30-second spike is invisible on it.
Use the raw series when investigating a short stall.

## Two timeouts, two meanings (2026-07-11)

The client timeout bounds one attempt; the request deadline bounds the whole
retry chain. Setting them equal makes the retries unreachable.

## Config precedence (established 2026-07-19)

The per-target override wins over the profile default, which wins over the
built-in. A value that "does nothing" is nearly always shadowed one level up.

## The idempotency key must cover the payload (2026-07-25)

Keying on the target alone collapses two genuinely different writes into one,
and the second is silently dropped.

## Ops table

| command | does |
|---|---|
| `status` | print health |
| `drain` | stop the producer, then drain |

## Glossary

Terms used above, with no date anywhere in the heading.
