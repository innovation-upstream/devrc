# /prune-memory — audit & aggressively shrink the project's auto-memory index

Goal: kill the recurring "MEMORY.md hit its cap again" fire-drill. Run an on-demand audit of the current project's `MEMORY.md`, then apply the aggressive cut methodology that works — so the index stays small instead of re-bloating daily.

Why this recurs: appending one line to `MEMORY.md` is lower-friction than putting a lesson in its real home, so the "🔴 Critical safety" and "Feedback" sections silently regrow with incident narratives and domain gotchas. This command is the periodic counter-pressure.

## Budgets (the contract)
- Index **target < 12 KB**, **hard cap ~24 KB** (content past the hard cap is silently dropped on load — every byte costs tokens every session).
- Section soft-caps: **🔴 Critical safety ≤ 10 bullets**, **Feedback ≤ 18 bullets**. Active work: prune to `ARCHIVE.md` the moment an item ships.
- Only three things cost per-session tokens: the loaded `MEMORY.md` index, `CLAUDE.md`, and the skill catalog one-liners. **Topic files and skill bodies cost 0 until recalled/triggered** — so migrating a lesson into a skill or leaving it in its topic file is free; only the index bullet is the cost.

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/memory-audit.py
```
(pass a memory dir or `MEMORY.md` path to audit a different project; with no arg it derives the current project's memory dir from `$PWD`). It prints: size vs budget, per-section byte-weights + bullet counts vs caps, **archive candidates** (Active-work bullets marked shipped/verified/merged/done), **fat bullets** (>250 B — trim targets), link integrity, and a one-line verdict.

If the verdict is "no prune needed", stop — report it and don't churn the file.

## 2. Back up first (the cut deletes/rewrites)
```bash
BK=/tmp/mem-prune-$(date +%s); mkdir -p "$BK"; cp -a <MEMORY_DIR>/. "$BK"/; echo "backed up to $BK"
```

## 3. Classify every over-budget bullet (the judgment cut)
For a big pass, dispatch read-only classifier subagents (one per fat section — Critical safety, Feedback) that check each bullet against `CLAUDE.md`, the global `~/.claude/RULES.md`, and the project's `.claude/skills/*/SKILL.md`, returning one verdict each:
- **DROP_REDUNDANT** — already in always-loaded `CLAUDE.md` or `RULES.md` (cite where). Remove from index.
- **DROP_ALREADY_IN_SKILL** — the durable lesson is already in a named skill (cite it). Remove.
- **MIGRATE_THEN_DROP** — a domain gotcha a skill *should* own but doesn't yet: add a ≤200-char line to that skill, then remove the index bullet.
- **KEEP_TIGHT** — genuinely cross-cutting (maps to NO skill) or an always-load safety tell you'd want even without invoking the skill: rewrite as a **≤140-char** one-liner, `[short title](slug.md) — <the single reusable tell>`. Strip incident narrative, PR numbers, and fixed-already detail — that lives in the topic file.
- **ARCHIVE** — resolved / now-just-history → move the bullet to `ARCHIVE.md`.

Bias aggressively toward DROP/MIGRATE/ARCHIVE. An **incident/postmortem is never a multi-line index bullet** — it's a `claudedocs/` doc + at most one ≤140-char cross-cutting tell.

## 4. Execute one atomic rewrite
- Rewrite `MEMORY.md` with the KEEP_TIGHT one-liners; remove DROP/MIGRATE bullets; append ARCHIVE bullets to a dated `## Archived <date>` section at the top of `ARCHIVE.md` (topic files persist — deleting an index bullet loses nothing; the `.md` file is still recall-on-demand).
- **Do NOT delete topic files** to save index bytes — it saves 0 per-session tokens and risks breaking `[[wikilinks]]`. Only delete a topic file if it's genuinely dead (superseded, zero inbound links).

## 5. Land the skill migrations (durable half)
Skill files live in the project repo → they need the repo's git workflow. For datapacket-talos that's a **throwaway worktree off `origin/trunk`** (never edit/commit the primary clone — see its CLAUDE.md rule #10); batch all skill edits into ONE commit, `git push origin HEAD:trunk`, and **verify each line is on `origin/trunk`** (`git show origin/trunk:<file> | grep`) before trusting the push. Skip any line the skill already covers.

## 6. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/memory-audit.py   # size under target, caps ✓, links resolve
```
Confirm: under the 12 KB target, both sections under cap, all links resolve, and (if you migrated) the skill lines are actually present on trunk. Report the before/after size, what moved where, and the backup path.

Pair: `/handoff` (put work-status in a doc instead of the index), the `CLAUDE.md` "Memory hygiene" section (the standing rule this command enforces).
