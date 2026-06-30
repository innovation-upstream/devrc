---
name: find-session
description: "Find a past Claude Code session by keyword — searches all transcripts and returns ranked sessions with project, date, branch, the opening message, matching snippets, and the resume command. Use to recover 'the session where we did X'."
argument-hint: "<term> [<term> …] [--project SUBSTR] [--since YYYY-MM-DD] [--any] [--limit N]"
allowed-tools: Bash, Read
---

# /find-session — recover a past session by keyword

Goal: kill the hand-typed "find the session where we did pr 235 / migrated the redis vpn" archaeology. Deterministic search over `~/.claude/projects/**/*.jsonl`.

Query: `$ARGUMENTS`.

## What to do

1. Run the search helper:
   ```bash
   python3 /home/zach/workspace/devrc/scripts/find-session.py $ARGUMENTS
   ```
   - Terms are **ANDed** by default (a session must match all). Add `--any` to OR them.
   - Quote a multi-word term to match it as a phrase: `"pr 235"`.
   - Narrow with `--project <substr>` (matches cwd/project), `--since YYYY-MM-DD`, `--limit N`.
   - Results are ranked: most distinct terms matched → most hits → most recent.

2. **Present the ranked hits** as the script returns them — each shows the date, project, git branch, the opening message, the matching snippet per term, and `claude --resume <id>`.

3. **Help pick the right one.** If several look plausible, point at the most likely from the genesis + snippets and say why. If the user wants the content (not to switch sessions), offer to read the transcript file directly with the printed `file:` path, or grep deeper.

Notes:
- This searches user-typed AND assistant text; pass `--all` to include tool output (noisier).
- To actually re-enter a session, the user runs `claude --resume <id>` themselves (a session can't resume into another from here).

Pair: `/resume` (re-enter from a handoff doc), `/handoff` (so next time there's a doc instead of archaeology).
