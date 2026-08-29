---
name: find-session
description: "Find a past Claude Code OR opencode session by keyword — searches both runtimes on both hosts and returns ranked sessions with the resume command. Use to recover 'the session where we did X'."
argument-hint: "<term> [<term> …] | --skill NAME [--project SUBSTR] [--since YYYY-MM-DD] [--any] [--limit N] [--claude-only|--opencode-only]"
allowed-tools: Bash, Read
---

# /find-session — recover a past session by keyword

Goal: kill the hand-typed "find the session where we did pr 235 / migrated the redis vpn" archaeology. Deterministic search over **two** corpora: Claude Code transcripts (`~/.claude/projects/**/*.jsonl`) and opencode sessions (the `opencode-stable.db` SQLite store), on **both hosts**.

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

2. **Present the ranked hits** as the script returns them — each shows the date, project,
   git branch, the opening message, the matching snippet per term, and the resume command
   (`claude --resume <id>`, or `opencode --session <id>` for an `[opencode]` row).

3. **Help pick the right one.** If several look plausible, point at the most likely from the genesis + snippets and say why. If the user wants the content (not to switch sessions), offer to read the transcript file directly with the printed `file:` path, or grep deeper.

Notes:
- This searches user-typed AND assistant text, plus the session's AI title; pass `--all` to
  widen the surface to tool inputs and tool output (noisier). 🔴 `--all` was **inert** until
  2026-08-24 — its handler sat behind a check an earlier `continue` had already made
  unreachable — so any earlier session that "searched with `--all`" searched the narrow
  surface. Re-run a query you trusted it for.
- 🔴 **"Did we ever USE skill X?" is `--skill NAME`, NEVER a keyword search.** A keyword
  cannot tell an invocation from the word in prose or in a path: measured 2026-08-29,
  `find-session.py signal` returned **666** sessions (nearly all `scripts/signal/tests/…`
  in test output) where `--skill signal` returns **1**. An investigation that used the
  keyword form concluded "never used operationally" and was wrong. `--skill` reads the
  per-record skill attribution, so it sees a skill that **auto-fired from its
  description** as well as one typed as `/name` — and most usage is the former (`browser`:
  50 attributed sessions, **0** ever typed). Works alone, with no search terms.
  Two limits, both deliberate: it counts SESSIONS, so a skill used only inside a
  dispatched **subagent** is not counted (`activity` had 15 such transcripts against 8
  sessions); and the **opencode corpus has no such attribution**, so that leg is SKIPPED
  and the omission is printed — do not read the result as fleet-wide.
- **Both agent runtimes are searched by default.** opencode rows are tagged `[opencode]`,
  carry `opencode:<host>` as their `file:`, and resume with `opencode --session <id>` (not
  `claude --resume`). `--claude-only` / `--opencode-only` restrict the corpus.
- 🔴 **A missing host is reported on stderr, never as a quiet zero.** Both hosts' opencode
  DBs are searched from either machine: whichever host you are on is read off local disk,
  the other over SSH. If a peer is unreachable the search prints `… its sessions are NOT in
  these results` — **surface that line to the user**, because the remaining hits then look
  like a complete answer and are not. Silence on stderr means both hosts answered.
  (Until 2026-08-26 the remote host was hardcoded to the workbench, so running from the
  workbench silently searched only itself — the laptop's sessions were invisible. Any
  earlier "no results" from the workbench is worth re-running.)
- The walk itself lives in `scripts/lib/transcript_search.py` and is shared with
  `check-clickup-addressed`; `subagents/` transcripts are excluded (they are not resumable
  sessions, and there are ~6x more of them than there are sessions).
- To actually re-enter a session, the user runs `claude --resume <id>` themselves (a session can't resume into another from here).

Pair: `/resume` (re-enter from a handoff doc), `/handoff` (so next time there's a doc instead of archaeology).
