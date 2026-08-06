---
name: odd-parity-info-string
description: SYNTHETIC fixture — well-formed CommonMark with ODD fence-marker parity.
---

<!--
FIXTURE, not a real skill. Deliberately NOT named SKILL.md.

Shape being reproduced: a documentation block that DISPLAYS a fence marker
carrying an info string. Under CommonMark a closing fence may not carry an info
string, so the ```action line below is content, not structure. The file has an
ODD number of fence markers and is nonetheless perfectly well-formed — the
exact shape a marker-PARITY heuristic calls "unclosed".

Expected: fence_ok is True, and every `##` heading stays visible.
-->

## Contract

The tool emits blocks like this — the inner marker is displayed, not opened:

```
```action
{"type": "apply_filter", "target": "svc-a"}
```

## Runbook

```bash
## this comment is inside a fence and must never register as a heading
tool apply --target svc-a
```

## After the fences

If this heading is missing from the audit, the fence walk swallowed the rest of
the file and every byte weight above it is wrong.
