# /prune-skill — audit & aggressively shrink a bloated `SKILL.md` body

Goal: stop skills from growing until the body displaces the work it was loaded for. Run an on-demand audit of a skill (or a whole `.claude/skills/` dir), then apply the same aggressive cut methodology `/prune-memory` uses on the index — one level down.

Why this recurs: a `SKILL.md` costs **0 tokens until its trigger fires, then ALL of it at once**. Nothing applies per-session pressure, so nobody notices, and every session appends. Measured 2026-08-02 in `datapacket-talos/.claude/skills/`: 67 skills = **3.13 MB**, up from 2.79 MB six days earlier while the skill COUNT went 65 → 66 — the growth was accretion into existing files, not new skills. Worst case `app-blocks/SKILL.md` = **726 KB ≈ 182k tokens**, which does not fit a 200k context at all; **37% of it (268,867 B) is 40 dated `### Session …` blocks.**

The cost model DIFFERS from `/prune-memory`: the memory index costs every session, a skill costs per *invocation*. That is not a licence to let it grow — a body big enough to crowd out the task is the same failure in a different currency, and it lands exactly when you are mid-task.

## Budgets (the contract)
- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven, not aspirational**: `scripts/browser-bridge/SKILL.md` holds a 21-op CLI and routes 11 reference topics (~113 KB) from **11,845 B**, enforced by `scripts/browser-bridge/tests/test_skill_size.py`. If a genuinely complex tool fits, a runbook does.
- **Reference files and `claudedocs/` docs cost 0 until opened** — verified 2026-08-02: invoking a skill loads ONLY `SKILL.md`; a sidecar mentioned by path was NOT loaded. Demotion is real token savings, not text-shuffling (~10,700 tokens/task on the browser skill).

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py            # $PWD/.claude/skills
python3 /home/zach/workspace/devrc/scripts/skill-audit.py path/to/SKILL.md
```
Prints: size vs budget per skill (worst first), per-section (H2, then H3 inside the fattest H2) byte weights, **dated-history blocks + a projected saving**, fat lines (>500 B), **reference-file integrity in both directions**, unclosed code fences, and a one-line verdict.

If the verdict is "no prune needed", **stop** — report it and don't churn the file.

## 2. Back up first (the cut deletes/rewrites)
```bash
BK=/tmp/skill-prune-$(date +%s); mkdir -p "$BK"; cp -a <SKILL_DIR>/. "$BK"/; echo "backed up to $BK"
```

## 3. Classify every over-budget block (the judgment cut)
For a big pass, dispatch read-only classifier subagents (one per fat H2) that check each block against the always-loaded files (`CLAUDE.md`, `~/.claude/RULES.md`) and the sibling skills, returning one verdict each:
- **EVICT_HISTORY** — dated session/changelog/"what we shipped" narrative → a `claudedocs/` doc, leaving at most **one ≤200-char durable tell**. Biggest single win; this is `/prune-memory`'s "work-STATUS doesn't belong in the index" rule one level down.
- **DEMOTE_TO_REFERENCE** — long-but-real procedures, tables, gotcha-depth → `reference/<topic>.md` beside the skill, with **ONE routing line** left in the core ("load it when…"). Free, exactly like the index's topic files.
- **DROP_REDUNDANT** — already in an always-loaded file or another skill; **cite where**, then delete.
- **MERGE_DUP** — the same gotcha restated across N appended sections → one statement.
- **KEEP_HOT** — the core: orientation, the hot commands, the 🔴 safety gotchas.

Bias aggressively toward EVICT/DEMOTE/DROP. A skill is a router, not an archive: if a block only matters for ONE kind of task, it belongs behind a pointer.

## 4. 🔴 Routing paths — get this wrong and the core routes to files the agent cannot open
Reference files are reached **BY PATH from the core**, and how you write that path depends on deployment:
- **devrc skills** are symlinked into `~/.claude/skills/<name>/` by `nix/home.nix`, which symlinks **only `SKILL.md`** — NOT `reference/`. A relative path does not resolve for the reader. Use **repo-absolute** paths (`~/workspace/devrc/scripts/<subsystem>/reference/<topic>.md`), as browser's core does.
- **Repo-local skills** (e.g. datapacket-talos `.claude/skills/<name>/`) ship the whole directory — relative `reference/<topic>.md` is fine.

The auditor checks both directions and warns on relative paths with no absolute base stated. In datapacket-talos the **doc-rot gate** (gate 0 of `scripts/gitops-delta-gate.sh` → `scripts/validate-skill-paths.sh`) also requires every backticked repo path to EXIST, with its conventions: a variable segment as `<var>` **in prose**, `$VAR` **inside a bash fence** (`<` is a redirect in shell), and a path in another repo marked as such.

## 5. Execute one atomic rewrite
Rewrite `SKILL.md` with the KEEP_HOT content plus routing lines; create the `reference/*.md` files; write the evicted history to a dated `claudedocs/` doc. **Do not delete a reference file to save bytes** — it saves 0 until opened. Do delete one that is genuinely dead (nothing routes to it — the auditor lists those as ORPHANED).

## 6. Land the change (per-repo git workflow)
- **datapacket-talos**: a **throwaway worktree off `origin/trunk`**, never the primary clone (its `CLAUDE.md` rule #10); `git push origin HEAD:trunk`, then verify with `git show origin/trunk:<file> | head`.
- **devrc**: feature branch + PR against `origin/main`.
- 🔴 **Never `git stash`** in either — `refs/stash` is repo-GLOBAL and shared across every worktree. **Never `git add -A`** — stage explicit paths.

## 7. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py <SKILL_DIR>
```
Confirm: under target (or at least under the hard cap), **every routing path resolves**, no orphans, no unclosed fences. Report before/after bytes, what moved where, and the backup path.

Pair: `/prune-memory` (the same cut on the per-session `MEMORY.md` index), and `scripts/browser-bridge/tests/test_skill_size.py` — the enforcement precedent: once a skill is lean, a byte-cap test is what keeps it lean.
