---
name: session-audit
description: Analyze recent Claude Code session transcripts for the current repo, then propose (and optionally implement) improvements to the project CLAUDE.md, slash commands/skills, token efficiency, and the permission allowlist. Use when the user wants to audit how Claude has been working in a repo and harden the setup for future sessions.
---

# /session-audit — Repo session retrospective

Turns a repo's own Claude Code history into concrete config improvements. Every
recommendation must be backed by a number from the transcript analysis, not a guess.

## Usage
```
/session-audit [days] [action]

days     lookback window (default 14)
action   analyze (default) | implement   (implement only after presenting findings)
```

## Step 1 — Run the analyzer
Defaults to the cwd's transcript dir, resolving `~/.claude/projects/<encoded-cwd>/`
automatically (path separators → `-`).

```bash
python3 ~/.claude/skills/session-audit/analyze.py --days 14 --top 30
# other repo: --cwd /path/to/repo   |   point directly: --project-dir ~/.claude/projects/<dir>
```

If it can't resolve the dir it prints the available project dirs — pick one, re-run with
`--project-dir`. Sections: tool usage, re-read files, re-fetched URLs, oversized
tool_results, errors/rejections, bash-verb frequency, edit targets, deduped user prompts.
Read them all before forming conclusions.

## Step 2 — Interpret across four axes

**A. Token efficiency** (re-reads, re-fetches, oversized results, errors, total tokens)
- Same file Read >1× across sessions → its structure belongs in CLAUDE.md.
- Same URL fetched >1× → cache the *fact* in CLAUDE.md, or fetch the page not an index.
- Oversized `tool_result`s → binaries being `cat`/Read, giant doc indexes, whole-file reads
  where a range or grep would do. Add a rule against the worst offender.
- Recurring identical errors → trapped knowledge (escaping quirks, missing paths, sudo
  constraints). Promote the fix to CLAUDE.md so the rediscovery loop stops.

**B. Project CLAUDE.md** (`./CLAUDE.md` — often the biggest win is that it's missing)
- Missing? That alone explains most re-reads. Draft one: deploy/build/test command, a
  one-line-per-file layout map of the re-read dirs, and the gotchas behind the recurring
  errors. Keep it ~40–60 lines — it loads every session, so high-signal, not a dump.

**C. Skills / slash commands** (`~/.claude/commands/`, `~/.claude/skills/`, `./.claude/`)
- Command too large or drifting? Heavy commands cost tokens on every invocation.
- Lore for a *different* repo embedded here → relocate it (move, don't delete — append to
  the owning repo first, leave a pointer).
- Stale references (bindings/scripts that no longer exist)? **Verify before pruning** —
  grep the live config/bindings; don't assume from the doc.
- Recurring workflow in the prompts with no skill → propose a small new skill.

**D. Permission allowlist** (`./.claude/settings.local.json`)
- Frequent read-only bash verbs not allowlisted → add them (cuts prompts).
- One-off cruft (store paths, giant one-shot `find`/`sed` strings) → remove.
- **Flag, don't silently grant, risky blanket rules**: `Bash(find:*)` also approves
  `find -delete`/`-exec rm`; `Bash(xdotool:*)` can drive any input. Add only if the user
  accepts the tradeoff, and never remove deliberate `sudo`/`bash` grants without asking.

## Step 3 — Report, prioritized by ROI
Group High/Medium/Low. For each: the evidence (the number), what to change, the file. Lead
with the highest-leverage item (usually a missing CLAUDE.md). Then offer to implement.

## Step 4 — Implement (only on `implement`, or after the user agrees)
- Write/extend `CLAUDE.md`; split or trim commands; clean the allowlist.
- Relocating knowledge between repos: append to the destination *before* deleting the source.
- Commit per the user's git rules: if on the default branch, branch first; don't push unless
  asked; `settings.local.json` is usually gitignored (local-only) — expected, not an error.
- Don't delete deployment-affecting files (symlinked scripts, etc.) without explicit
  confirmation — surface them as a separate follow-up.

## ⚠ Skills are devrc-managed — edit the SOURCE, not `~/.claude/skills/`
Since `9d8b0bd` (2026-07-29) the doc/script skills live in **`~/workspace/devrc/claude/skills/`**,
symlinked **READ-ONLY** into `~/.claude/skills/` via `home.file.".claude/skills"` (recursive).
So when this audit proposes editing a skill:
- **Edit `~/workspace/devrc/claude/skills/<name>/` → `home-manager switch --flake ~/workspace/devrc --impure` → push; the other host `git pull`s + switches.** Never edit `~/.claude/skills/` directly (read-only symlink — the change won't ship and may error).
- **Excluded** (NOT devrc-managed): `clickup` (installed Node project with `node_modules`, its own flake + `accounts.json`) and `browser` (symlinked from `scripts/browser-bridge`). Edit those in place.
- **home-manager gotcha:** `home.file` `recursive=true` + `force=true` does **NOT** clobber a pre-existing REAL directory tree — the switch silently leaves those files unlinked, so edits there never ship. Fix: `diff -rq` the real dir against the store generation (`readlink -f` a working symlink to locate the gen); if identical, `rm -rf` the conflicting real dirs and re-`switch`.

## Boundaries
**Will:** parse the repo's transcripts; quantify waste; draft a CLAUDE.md; trim/relocate skill
content; propose allowlist edits; implement on request.
**Will not:** invent metrics without transcript evidence; grant risky permissions silently;
prune "stale" references without verifying they're unused; delete deploy-affecting files
unprompted; push commits unless asked.
