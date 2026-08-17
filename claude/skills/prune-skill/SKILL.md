---
name: prune-skill
description: "Audit and aggressively shrink a bloated SKILL.md body so it stops displacing the task it was loaded for. Runs scripts/skill-audit.py, demotes detail to reference/, evicts dated history, rewrites atomically, re-measures. Use when asked to prune/shrink/trim a skill, when a skill body is huge, or when its reference paths have rotted."
argument-hint: "[SKILL_DIR | path/to/SKILL.md] — optional; defaults to $PWD/.claude/skills"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Agent
---

# prune-skill — audit & aggressively shrink a bloated `SKILL.md` body

Goal: stop a skill body from displacing the work it was loaded for. Audit a skill (or a whole `.claude/skills/` dir), then apply the `prune-memory` skill's cut methodology one level down.

Why this recurs: a `SKILL.md` costs **0 tokens until its trigger fires, then ALL of it at once**. Nothing applies per-session pressure, so every session appends. Measured in `datapacket-talos/.claude/skills/`: 67 skills = **3.13 MB**, +0.34 MB in six days on a count of 65 → 66 — accretion, not new skills. Worst case `app-blocks/SKILL.md` = **742 KB ≈ 186k tokens** (does not fit a 200k context), **43% dated history** (42 of 48 blocks `### Session …`). The cost model DIFFERS from the `prune-memory` skill — the index costs every session, a skill per *invocation* — which is no licence to grow: a body that crowds out the task is the same failure in another currency, landing mid-task.

## Budgets (the contract)
- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven, not aspirational**: `scripts/browser-bridge/SKILL.md` routes a 21-op CLI and 11 reference topics (~113 KB) from **11,845 B**, enforced by its `tests/test_skill_size.py`. If a complex tool fits, a runbook does.
- **Reference files and `claudedocs/` docs cost 0 until opened** — verified: invoking a skill loads ONLY `SKILL.md`, not a sidecar mentioned by path. Demotion is real token savings, not text-shuffling (~10,700 tokens/task on the browser skill).
- 🔴 **12 KB is a SKILL budget — check what governs THIS file before executing the auditor's number.** It demanded "cut ~21,280 B" from `claude/RULES.md`, which is not a skill body: always-loaded, and under its own tighter gate — **`scripts/tests/test_rules_size.py` OWNS the cap and the headroom floor; read them there, never from here.** (This line used to restate them and went stale, asserting a cap two revisions old — the failure the devrc rule about that gate exists to prevent, committed by the document telling you to avoid it.) That gate PASSES, so the right pass there is headroom (demote narrative to `claude/RULES-ARCHIVE.md`), not a 12 KB cut that guts rule scope. And `RULES-ARCHIVE.md` is the eviction SINK: ungated, demand-loaded, 0 B until opened — pruning it deletes the narrative demoted there to keep the core lean. Report the mismatch, don't execute the number; a "within budget" verdict (§1) stays authoritative.

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py            # $PWD/.claude/skills
python3 /home/zach/workspace/devrc/scripts/skill-audit.py path/to/SKILL.md
```
Prints: size vs budget per skill (worst first), per-section byte weights (H2, then H3 in the fattest H2), **dated-history blocks + projected saving**, fat lines (>500 B), **reference-file integrity both directions**, **numbered-corpus integrity**, unclosed fences, and a one-line verdict. If it says "no prune needed", **stop** — report it, don't churn the file.

🔴 **But a SMALL over-budget number does not mean a small defect — the auditor measures bytes, only a read finds rot.** app-blocks audited at 144 B over (1.2%), which reads as "trim a line"; the actual finding was a 1,931 B section titled *"direct mutations to ephemeral state… if the DB is rebuilt, they vanish"* in which **two of the four entries were retired**, one struck through and one marked `NO LONGER LOAD-BEARING`. Half a section headed *re-apply these* described state that must **not** be re-applied, and both entries were already stated in a reference file whose own banners pointed back at it. Read the fattest sections whatever the byte verdict says.

## 2. Back up first (the cut deletes/rewrites)
```bash
BK=/tmp/skill-prune-$(date +%s); mkdir -p "$BK"; cp -a <SKILL_DIR>/. "$BK"/; echo "backed up to $BK"
```

## 3. Classify every over-budget block (the judgment cut)
For a big pass, dispatch read-only classifier subagents (one per fat H2) checking each block against the always-loaded files (`CLAUDE.md`, `~/.claude/RULES.md`) and sibling skills:
- **EVICT_HISTORY** — **work-status** narrative (`### Session …`, `Changelog`, "what we shipped") → a `claudedocs/` doc, leaving at most **one ≤200-char durable tell**. This is the `prune-memory` skill's "work-STATUS doesn't belong in the index", one level down.
  🔴 **A date in a heading is NOT this verdict.** `## Common silent-failure modes (from the 2026-05-22 audit)` is durable guidance citing a date; evicting it guts the skill. The auditor reports the two separately for that reason — across the 67-skill datapacket corpus **app-blocks holds 45 work-status headings and every other skill ZERO**, while carrying 8–17 dated-but-topical ones. Read its "dated lessons" as DEMOTE, never eviction, candidates.
- **DEMOTE_TO_REFERENCE** — long-but-real procedures, tables, gotcha-depth → `reference/<topic>.md` beside the skill, with **ONE routing line** left in the core ("load it when…").
  🔴 **If the block is NUMBERED and cited by number, the numbers are an API — FREEZE them.** Items cited as "gotcha #178" are referenced from the core, from sibling reference files and from OTHER skills, so a split that renumbers each file 1..n breaks every citation while leaving all PATHS valid — no path gate can see it. Keep the original global numbers (they will be non-contiguous inside each file, which is correct), say so in a banner at the top of every shard, and never renumber. app-blocks survives a 9-file split this way: 200 items, numbered 1–200 with zero gaps, 150 citations, zero dangling. Renumbering its shards strands **53** citations instantly.
  🔴 **Do not put a COUNT in a routing line.** "(18 gotchas)" is a number no gate checks and every append invalidates — app-blocks' table labelled a 20-item file `(18)` and its headline said `198` for 200. Describe what the file is *for*, not how much is in it.
- **DROP_REDUNDANT** — already in an always-loaded file or another skill; **cite where**, then delete.
- **MERGE_DUP** — the same gotcha restated across N appended sections → one statement.
- **KEEP_HOT** — the core: orientation, the hot commands, the 🔴 safety gotchas.

Bias aggressively toward EVICT/DEMOTE/DROP. A skill is a router, not an archive: a block that matters for ONE kind of task belongs behind a pointer.

## 4. 🔴 Routing paths — get these wrong and the core routes to files nobody can open
Reference files are reached **BY PATH from the core**; how you write it depends on deployment:
- **devrc skills under `claude/skills/<name>/`**: `nix/home.nix` maps the directory with
  `home.file … recursive = true`, so **`reference/` DOES ship** — it lands at
  `~/.claude/skills/<name>/reference/<topic>.md` (verified: `activity`, `clawgate`,
  `initiatives`, `mailbox`, `tekton`, `auditloop` all have live `reference/` dirs there).
  Give BOTH the deployed path and the repo source, as the existing skills do:
  ``reference/<topic>.md`` at `~/.claude/skills/<name>/reference/`, source
  `~/workspace/devrc/claude/skills/<name>/reference/`.
- **The `mkOutOfStoreSymlink` exceptions** (`browser` → `scripts/browser-bridge/`,
  `dl-router` → `scripts/dl-router/`) symlink only `SKILL.md` + the CLI, **not** a
  `reference/` subtree — so those cores MUST use repo-absolute paths
  (`~/workspace/devrc/scripts/<subsystem>/reference/<topic>.md`), as browser's core does.
- **Repo-local skills** (a project's own `.claude/skills/<name>/`) ship the whole
  directory, so the file is always THERE — but 🔴 **a bare `reference/<topic>.md`
  is still resolved by the reader against the CWD, not against the skill's own
  directory, and is simply not found** (measured on datapacket-talos app-blocks
  2026-08-04). Write the repo-root-relative form
  `.claude/skills/<name>/reference/<topic>.md`, **or** keep short table entries
  and state the expansion once in a preamble above the table — which is what
  app-blocks does. All three repo-local skills that have a `reference/` dir use
  one of those two forms; **none** relies on a bare relative path. Shipping and
  resolving are separate failures: the nix question below is about whether the
  file exists at all, this one bites when it does.

The auditor checks both directions, warning on relative paths with no absolute base.
Independently of the auditor: every backticked repo path in a skill should EXIST. Write a
variable segment as `<var>` **in prose** and `$VAR` **inside a bash fence** (`<` is a
redirect in shell), and mark a cross-repo path as such.

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
Confirm: under target (at least under the hard cap), **every routing path resolves**, no orphans, no unclosed fences, and — if you split a numbered corpus — **`numbered-corpus integrity` reports every citation resolving with no duplicates**. Duplicate numbers across shards are the signature of a per-file renumber; dangling citations are what that costs. Report before/after bytes, what moved where, and the backup path.

🔴 **Open one demoted file and one routing line by hand before calling it done.** `skill-audit.py` resolves routing paths against the skill directory — which always succeeds — so its ✓ is not evidence that a reader following the core from a repo root will find the file. That is the §4 trap, and the audit cannot see it.

Pair: the `prune-memory` skill (the same cut on the per-session `MEMORY.md` index); `test_skill_size.py` above is the precedent — once a skill is lean, a byte-cap test keeps it lean.
