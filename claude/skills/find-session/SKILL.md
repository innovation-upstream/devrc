---
name: find-session
description: "Find a past Claude Code OR opencode session by keyword — searches both runtimes on both hosts and returns ranked sessions with the resume command. Use to recover 'the session where we did X'."
argument-hint: "<term> [<term> …] | --skill NAME [--live [--deep] [--tail N]] [--limit N] [--project SUBSTR (archive only)] [--since YYYY-MM-DD (archive only)] [--any (archive only)] [--claude-only|--opencode-only]"
allowed-tools: Bash, Read
---

# /find-session — recover a past session by keyword

Goal: kill the hand-typed "find the session where we did pr 235 / migrated the redis vpn" archaeology. Deterministic search over **two** corpora: Claude Code transcripts (`~/.claude/projects/**/*.jsonl`) and opencode sessions (the `opencode-stable.db` SQLite store), on **both hosts**.

Query: `$ARGUMENTS`.

## 🔴 If the user thinks it might still be RUNNING, use `--live` — 1.8 s, not 30 s

Measured 2026-08-28: the transcript walk below takes **30.1 s**; the live cross-host tmux
scan takes **1.82 s**, and its rows already carry the task, the label, the hotkey, the
status, `waiting_probable`, the path and the session id. For *"find that thing I lost
track of, is it still running, which window, where did it leave off"* the archive is the
**wrong instrument** — it searches the past for a question about now.

```bash
python3 /home/zach/workspace/devrc/scripts/find-session.py <terms> --live [--tail 80]
```

- **Live first, archive only as a fallback.** If any live window matched, the 30 s walk is
  skipped entirely and the output says so. `--deep` runs both.
- **Matched against `task`, `label` and `codename` — deliberately NOT `path`.** Measured on
  a 72-row scan: one substring hit **1** row on `task` and **29** on `path`, because nearly
  every window shares a repo path. The LIVE header prints the field list it searched, so
  surface it if a query the user expected to hit found nothing.
- **`--tail N` answers "where did it leave off"** by printing that window's scrollback —
  only when the match resolved to **exactly one** window. 🔴 **It REFUSES otherwise**:
  several matched → exit 3, listing the candidates with a ready-made command for each
  (*do not pick one for them* — a scrollback printed from the wrong window reads as a
  correct answer); none matched → exit 3; the fleet was not measured → exit 4.
- **Quote `hotkey_display` from the output verbatim; never re-derive a chord.** `M-v` opens
  `scratch3`/violet and `M-V` opens `scratch4`/**Vapor** — different sessions. A previous
  run read `hotkey: v` and answered `Alt+Shift+V`, sending the operator to the wrong window.
- 🔴 **An unreachable host is never "not running".** `LIVE: SCAN FAILED` / `LIVE: NO HOST
  ANSWERED` / a `NOT searched: <host>` line each mean the fleet was **not measured** —
  say so in your answer rather than reporting the empty result as an absence. The tool
  falls back to the archive in all three cases and labels why.
- Archive hits are annotated **`<LIVE>` / `<CLOSED>` / `<UNMEASURED>`** by joining on
  `claude_session_id` against a second, *unfiltered* live scan. 🔴 **A `<CLOSED>` needs
  FULL coverage; a `<LIVE>` does not.** Finding the id on a host that answered proves the
  session is live. Failing to find it proves nothing while any host is down — so with the
  laptop asleep (this fleet's common degraded state) a miss reads `<UNMEASURED>`, the
  ARCHIVE block prints `live/closed state is PARTIAL`, and the JSON carries
  `archive.live_coverage_complete: false` beside `live_ids_measured: true`. Do not report
  an `<UNMEASURED>` hit as finished.
- 🔴 **These flags reach the ARCHIVE leg ONLY** — `--skill`, `--any`, `--project`, `--since`,
  `--claude-only`, `--opencode-only`, `--all` — and the tool names them on stderr. (That
  list is `ARCHIVE_ONLY_FLAGS` in the script and is pinned against this line by
  `test_find_session_skill_contract.py`; it carries no count, because a count is a claim
  nothing enforces.) Surface
  that notice: an empty LIVE section under `--any` is a measured absence under semantics
  the user did not ask for. 🔴 **A CORPUS selector is worse than "not filtered":**
  `--opencode-only --live` shows tmux windows of any runtime, and if the live leg matches
  the archive never runs, so **the corpus the user chose is never searched**. The notice
  says to pass `--deep`; do that rather than reporting the live rows as the answer.
  `--limit` DOES bound the LIVE section (it prints `showing N of M`) but deliberately does
  **not** narrow the `--tail` ambiguity check; `--limit` below 1 is a usage error (it used
  to mean "everything" on one leg and "nothing" on the other).
- 🔴 **A failed `--tail` is not an empty window.** `session-manager tail` returning 2/4/5
  (window closed between the scan and the tail, host gone, no tmux server) prints
  `TAIL: FAILED — … exited N` and exits 4, with `tail.rc` / `tail.ok` in the JSON. Only
  rc 0 and rc 3 are measured; rc 3 renders as an explicitly MEASURED empty pane.
- **Exit codes.** 🔴 These sentences are `EXIT_CONTRACT` in `scripts/find-session.py`,
  copied verbatim and pinned by `scripts/tests/test_find_session_skill_contract.py` — which
  also pins each one against the behaviour it describes. Do not reword them here alone; an
  earlier hand-written version of this table shipped two claims the code contradicted.
- `0` — the run completed. NOT a claim that anything matched — an empty LIVE section and an empty ARCHIVE section both exit 0. 🔴 NOR a claim about coverage: a `--tail` that resolved to ONE window exits 0 even when a host did not answer, so another window may match on the host that was never asked. This is the code a caller ACTS on — read `tail.coverage_complete` before treating the resolution as unique.
- `2` — bad arguments: `--tail` without `--live`, `--limit` below 1, an unparseable `--since`, a query that names nothing (no terms and no `--skill`, or a `--skill` that canonicalises to empty), or `--skill` with `--opencode-only` — that corpus carries no skill attribution, so the combination has no answer rather than an empty one.
- `3` — `--tail` ONLY: it could not resolve to exactly one live window — several matched, or none did on a fleet where every host answered. It carries NO claim about coverage; the candidate list may be incomplete, and `tail.coverage_complete` is the field that says so.
- `4` — `--tail` ONLY: something the tail needed was NOT measured — the live scan failed or no host answered, or `session-manager tail` itself failed (rc 2/4/5), or nothing matched while a host was unreachable. Without `--tail` a failed scan still exits 0 and says so in the LIVE section.
- Branch on `tail.ok` / `tail.rc` / `tail.coverage_complete` in `--json` rather than on the
  code alone — `tail.coverage_complete` is `null` when the scan never ran, `false` when it
  ran and a host was missing. `--live` composes with `--json`, which then emits
  `{live, archive, tail}` instead of the bare array.
- 🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** `--live --json` passes
  the live rows through verbatim, and one of them is `unsent_prompt` — text the operator
  typed and never sent. `--tail` output is a whole pane of it. devrc is a **PUBLIC** repo,
  as is every `claudedocs/` note, commit message, PR body and fixture an agent writes into
  it. Report either as a **count, a length or a shape**, never verbatim. (The human `--live`
  section deliberately does not print `unsent_prompt` at all.)

The live scan is `session-manager scan --json --lean --no-ch --match …`; the match
predicate lives THERE, not in `find-session.py`, so the two tools cannot disagree. Details:
`~/.claude/skills/session-manager/reference/payload-contract.md`.

## What to do (the archive search)

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
- 🔴 **"Did we ever USE skill X?" is `--skill NAME`, NEVER a keyword search.** A keyword
  cannot tell an invocation from the word written in prose or in a path: measured
  2026-08-29, `find-session.py signal` returned **666** sessions where `--skill signal`
  returns **1**. An investigation that used the keyword form concluded "never used
  operationally" and was wrong. `--skill` matches on RECORDED IDENTITY across all three
  invocation routes — the per-record attribution (the only one that sees a skill
  **auto-firing from its description**, and most usage is that: `browser` = 50 attributed
  sessions, **0** ever typed), an explicit `Skill` tool call, and a typed `/name`.
  🔴 It **forces the archive leg** even when `--live` matched, because the live scan has no
  skill-attribution axis — a window's task/label/codename cannot say which skill ran in it.
  Limits, all deliberate: it counts SESSIONS, so a skill used only inside a dispatched
  **subagent** is not counted; the **opencode corpus has no per-record attribution**, so
  that leg is SKIPPED and the omission is printed to stderr — do not read the result as
  fleet-wide; a typed **built-in** matches too (`--skill login` finds sessions that typed
  `/login`); and a path-qualified identity is recorded by its SKILL, so
  `--skill apps/api:deploy` and `--skill apps/web:deploy` both match `deploy`.
- This searches user-typed AND assistant text, plus the session's AI title; pass `--all` to
  widen the surface to tool inputs and tool output (noisier). 🔴 `--all` was **inert** until
  2026-08-24 — its handler sat behind a check an earlier `continue` had already made
  unreachable — so any earlier session that "searched with `--all`" searched the narrow
  surface. Re-run a query you trusted it for.
- **Both agent runtimes are searched by default.** opencode rows are tagged `[opencode]`,
  carry `opencode:<host>` as their `file:`, and resume with `opencode --session <id>` (not
  `claude --resume`). `--claude-only` / `--opencode-only` restrict the corpus.
- 🔴 **BOTH corpora are now searched on BOTH hosts, and that sentence used to be HALF
  FALSE.** `opencode` sessions have been searched cross-host since 2026-08-26; Claude
  transcripts were **local-host only** while this skill's description already claimed
  "both hosts". That gap is what made a workbench run report a laptop-only skill as never
  used. The Claude peer leg runs the PEER'S OWN `transcript_search`, so the hosts cannot
  drift into different semantics silently — and a peer that has not been shipped yet says
  so by name rather than answering without the filter:

      find-session: peer laptop (10.42.0.100): peer is running an older
      transcript_search with no --skill support (run ship.sh) — its Claude
      sessions are NOT in these results

  Remote hits are tagged **`[claude-remote]`**. 🔴 Surface any `peer … NOT in these
  results` line to the user: the remaining hits then look like a complete answer and are
  not.
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

Pair: `/resume` (re-enter from a handoff doc), `/handoff` (so next time there's a doc instead of archaeology), `session-manager` (the live fleet in full — this skill's `--live` is a one-question slice of it).
