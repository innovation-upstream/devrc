---
name: odd-parity-nested-lang
description: SYNTHETIC fixture — a ```lang marker displayed INSIDE an open fence.
---

<!--
FIXTURE, not a real skill. Deliberately NOT named SKILL.md.

Shape being reproduced: a heredoc inside a ```bash block that writes out
another ```bash block. The inner marker carries an info string, so it can
neither close the outer fence nor open a new one — it is content. ODD marker
parity, well-formed CommonMark.

This also carries the mechanism behind the original false positive: a shell
`## 3. …` COMMENT inside the fence. If the fence walk breaks, that comment is
read as an H2 and silently re-partitions the whole file — so it is written at
H2 depth deliberately, to land in the `h2` list the test pins.

Expected: fence_ok is True, the shell comment is not a heading, and the
headings after the fence stay visible.
-->

## Runbook

```bash
# 1. write the snippet the docs embed
cat <<'EOF' > snippet.md
```bash
tool status --target svc-a
EOF
## 3. state check — NOT a heading, this line is inside the fence
tool verify
```

## Still visible

The heading above must survive the fence. So must the one below.

## Also still visible

Tail content.
