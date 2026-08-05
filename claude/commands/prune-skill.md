# /prune-skill — audit & aggressively shrink a bloated `SKILL.md` body

Goal: stop a skill body from displacing the work it was loaded for. Audit a skill (or a whole `.claude/skills/` dir), then apply `/prune-memory`'s cut methodology one level down.

Why this recurs: a `SKILL.md` costs **0 tokens until its trigger fires, then ALL of it at once**. Nothing applies per-session pressure, so every session appends. Measured in `datapacket-talos/.claude/skills/`: 67 skills = **3.13 MB**, +0.34 MB in six days on a count of 65 → 66 — accretion, not new skills. Worst case `app-blocks/SKILL.md` = **742 KB ≈ 186k tokens** (does not fit a 200k context), **43% dated history** (42 of 48 blocks `### Session …`). The cost model DIFFERS from `/prune-memory` — the index costs every session, a skill per *invocation* — which is no licence to grow: a body that crowds out the task is the same failure in another currency, landing mid-task.

## Budgets (the contract)
- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven, not aspirational**: `scripts/browser-bridge/SKILL.md` routes a 21-op CLI and 11 reference topics (~113 KB) from **11,845 B**, enforced by its `tests/test_skill_size.py`. If a complex tool fits, a runbook does.
- **Reference files and `claudedocs/` docs cost 0 until opened** — verified: invoking a skill loads ONLY `SKILL.md`, not a sidecar mentioned by path. Demotion is real token savings, not text-shuffling (~10,700 tokens/task on the browser skill).
- 🔴 **12 KB is a SKILL budget — check what governs THIS file before executing the auditor's number.** It demanded "cut ~21,280 B" from `claude/RULES.md`, which is not a skill body: always-loaded, under its own tighter gate (`scripts/tests/test_rules_size.py`, 34,500 B cap / 900 B headroom) that it PASSES — so the right pass there is headroom (demote narrative to `claude/RULES-ARCHIVE.md`), not a 12 KB cut that guts rule scope. And `RULES-ARCHIVE.md` is the eviction SINK: ungated, demand-loaded, 0 B until opened — pruning it deletes the narrative demoted there to keep the core lean. Report the mismatch, don't execute the number; a "within budget" verdict (§1) stays authoritative.

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py            # $PWD/.claude/skills
python3 /home/zach/workspace/devrc/scripts/skill-audit.py path/to/SKILL.md
```
Prints: size vs budget per skill (worst first), per-section byte weights (H2, then H3 in the fattest H2), **dated-history blocks + projected saving**, fat lines (>500 B), **reference-file integrity both directions**, unclosed fences, and a one-line verdict. If it says "no prune needed", **stop** — report it, don't churn the file.

## 2. Back up first (the cut deletes/rewrites)
```bash
BK=/tmp/skill-prune-$(date +%s); mkdir -p "$BK"; cp -a <SKILL_DIR>/. "$BK"/; echo "backed up to $BK"
```

## 3. Classify every over-budget block (the judgment cut)
For a big pass, dispatch read-only classifier subagents (one per fat H2) checking each block against the always-loaded files (`CLAUDE.md`, `~/.claude/RULES.md`) and sibling skills:
- **EVICT_HISTORY** — **work-status** narrative (`### Session …`, `Changelog`, "what we shipped") → a `claudedocs/` doc, leaving at most **one ≤200-char durable tell**. `/prune-memory`'s "work-STATUS doesn't belong in the index", one level down.
  🔴 **A date in a heading is NOT this verdict.** `## Common silent-failure modes (from the 2026-05-22 audit)` is durable guidance citing a date; evicting it guts the skill. The auditor reports the two separately for that reason — across the 67-skill datapacket corpus **app-blocks holds 45 work-status headings and every other skill ZERO**, while carrying 8–17 dated-but-topical ones. Read its "dated lessons" as DEMOTE, never eviction, candidates.
- **DEMOTE_TO_REFERENCE** — long-but-real procedures, tables, gotcha-depth → `reference/<topic>.md` beside the skill, with **ONE routing line** left in the core ("load it when…").
- **DROP_REDUNDANT** — already in an always-loaded file or another skill; **cite where**, then delete.
- **MERGE_DUP** — the same gotcha restated across N appended sections → one statement.
- **KEEP_HOT** — the core: orientation, the hot commands, the 🔴 safety gotchas.

Bias aggressively toward EVICT/DEMOTE/DROP. A skill is a router, not an archive: a block that matters for ONE kind of task belongs behind a pointer.

## 4. 🔴 Routing paths — get these wrong and the core routes to files nobody can open
Reference files are reached **BY PATH from the core**; how you write it depends on deployment:
- **devrc skills**: `nix/home.nix` symlinks **only `SKILL.md`** into `~/.claude/skills/<name>/` — NOT `reference/`, so a relative path does not resolve for the reader. Use **repo-absolute** paths (`~/workspace/devrc/scripts/<subsystem>/reference/<topic>.md`), as browser's core does.
- **Repo-local skills** (e.g. datapacket-talos `.claude/skills/<name>/`) ship the whole directory — relative `reference/<topic>.md` is fine.

The auditor checks both directions, warning on relative paths with no absolute base. In datapacket-talos the **doc-rot gate** (gate 0 of `scripts/gitops-delta-gate.sh` → `scripts/validate-skill-paths.sh`) also requires every backticked repo path to EXIST: a variable segment is `<var>` **in prose**, `$VAR` **inside a bash fence** (`<` is a redirect in shell), and a cross-repo path is marked as such.

## 5. Execute one atomic rewrite
Rewrite `SKILL.md` as KEEP_HOT plus routing lines; create the `reference/*.md` files; write evicted history to a dated `claudedocs/` doc. **Do not delete a reference file to save bytes** — it saves 0 until opened. Do delete a genuinely dead one (nothing routes to it; the auditor lists those as ORPHANED).

## 6. Land the change (per-repo git workflow)
- **datapacket-talos**: a **throwaway worktree off `origin/trunk`**, never the primary clone (its `CLAUDE.md` rule #10); `git push origin HEAD:trunk`, verify `git show origin/trunk:<file> | head`.
- **devrc**: feature branch + PR against `origin/main`.
- 🔴 **Never `git stash`** in either — `refs/stash` is repo-GLOBAL and shared across every worktree. **Never `git add -A`** — stage explicit paths.

## 7. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py <SKILL_DIR>
```
Confirm: under target (at least under the hard cap), **every routing path resolves**, no orphans, no unclosed fences. Report before/after bytes, what moved where, and the backup path.

Pair: `/prune-memory` (the same cut on the per-session `MEMORY.md` index); `test_skill_size.py` above is the precedent — once a skill is lean, a byte-cap test keeps it lean.
