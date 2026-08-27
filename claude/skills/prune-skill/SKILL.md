---
name: prune-skill
description: "Audit and aggressively shrink a bloated SKILL.md body so it stops displacing the task it was loaded for — demotes detail to reference/, evicts dated history, re-measures. Use when asked to prune/shrink/trim a skill, when a skill body is huge, or when its reference paths have rotted."
argument-hint: "[SKILL_DIR | path/to/SKILL.md] — optional; defaults to $PWD/.claude/skills"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Agent
---

# prune-skill — audit & aggressively shrink a bloated `SKILL.md` body

Stop a skill body from displacing the work it was loaded for: a `SKILL.md` costs **0 tokens until its trigger fires, then ALL of it at once**, so nothing applies per-session pressure and every session appends — one corpus hit **742 KB in a single skill**, which does not fit a 200k context. Audit a skill (or a whole `.claude/skills/` dir), then apply the `prune-memory` skill's cut methodology one level down.

**Reference topics** — `reference/<topic>.md` at `~/.claude/skills/prune-skill/reference/`, source `~/workspace/devrc/claude/skills/prune-skill/reference/`:

| Load when | File |
|---|---|
| Before running the staleness pass (§0) — axes + the false-finding traps | `~/.claude/skills/prune-skill/reference/staleness-pass.md` |
| Before verifying (§7) — survival check, gap audit, control design | `~/.claude/skills/prune-skill/reference/verification-protocol.md` |
| Pruning an ALWAYS-LOADED file; landing + re-sync; dispatching prune subagents | `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md` |
| Before your first split (§3) — what cites in, frozen numbers, routing-line traps | `~/.claude/skills/prune-skill/reference/classification-rules.md` |
| A budget number is load-bearing, or the file may not be a skill | `~/.claude/skills/prune-skill/reference/budgets-and-scope.md` |

## Budgets (the contract)
- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts. `scripts/browser-bridge/tests/test_skill_size.py` **owns** the ceiling and headroom floor — read the numbers there, never restate them.
- 🔴 **Check what governs THIS file first — 12 KB is a SKILL budget.** Another repo's gate may be a ratchet with an allowance (talos-infra gate 11), passing bodies this tool calls RED and blocking ones it calls fine. `skill-audit.py` marks a tree it does not govern; believe that mark, not the number.
- 🔴 **A low core-to-reference ratio points at CANDIDATES, not a target** — its floor is shape-dependent: a tool-shaped skill routes many times its weight, a method-shaped one may sit near 2x. Rationale, measurements and history are demotable in any shape.
- 🔴 **Never prune an eviction SINK** (`claude/RULES-ARCHIVE.md`, a `claudedocs/` doc) — it is ungated and demand-loaded, so pruning it deletes what a prior prune put there. §3's evict-bias does not apply to one.
- 🔴 **ALWAYS-LOADED files (`CLAUDE.md`, `claude/RULES.md`) are a DIFFERENT problem — this playbook does not apply, and such a pass may legitimately end LARGER.** Read `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md` BEFORE touching one.
- Ratchet, landing over target, the measured ratio table and the browser-bridge existence proof: `~/.claude/skills/prune-skill/reference/budgets-and-scope.md`.

## 0. 🔴 Staleness pass FIRST — a prune preserves rot BY CONSTRUCTION
Verbatim slicing plus a survival check *guarantee* stale content survives intact, and no path gate proves a claim TRUE. ~6 tool calls. **Run it even when §1 says no prune is needed.**
- 🔴 **Expect MORE false findings than real ones (measured ~6:1); confirm each before "fixing" it.** An empty result is not a rename — go to the DEFINING surface, carry a positive control, and suspect your own instrument first. **A documented filter beats a probe.**
- 🔴 **Fix EVERY copy.** A prune multiplies the copies of every claim — core, sidecar, always-loaded file. Grep the corrected token across the whole skill dir; a core saying "X is stale" beside a sidecar still asserting X is worse than either alone.
- A finding needing a DECISION is FLAGGED, not silently fixed. Findings go in the commit message.

Axes (live objects, cross-repo paths, metrics, load-bearing claims) and the traps that manufacture false findings: `~/.claude/skills/prune-skill/reference/staleness-pass.md`.

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py            # $PWD/.claude/skills
python3 /home/zach/workspace/devrc/scripts/skill-audit.py path/to/SKILL.md
```
Prints: per-skill size vs budget, per-section byte weights, dated-history blocks, fat lines (>500 B), **reference integrity both directions**, **numbered-corpus integrity**, unclosed fences, and a verdict.

If it says "no prune needed", **stop CUTTING** — it is a verdict about **BYTES only**: §0's staleness pass is still due.

🔴 **A small over-budget number does not mean a small defect** — the auditor measures bytes, only a read finds rot (case: `~/.claude/skills/prune-skill/reference/staleness-pass.md`). 🔴 **Read the heading TREE before trusting the section weights** — they are a function of it.

## 2. Back up first (the cut deletes/rewrites)
🔴 **Chain the success message with `&&` and count the files** — `cp …; echo "backed up"` prints success even when the copy failed, so a broken backup announces itself as a good one. This is the safety net for §5.
```bash
SKILL_DIR=.claude/skills/gitops-gate          # ← the skill you are pruning
BK=/tmp/skill-prune-$(date +%s); mkdir -p "$BK"
cp -a "$SKILL_DIR"/. "$BK"/ && echo "backed up to $BK: $(find "$BK" -type f | wc -l) file(s)"
```

## 3. Classify every over-budget block (the judgment cut)
Dispatch read-only classifier subagents (one per fat H2) on a big pass, checking each block against the always-loaded files and sibling skills:
- **EVICT_HISTORY** — **work-status** narrative (`### Session …`, `Changelog`, "what we shipped") → a `claudedocs/` doc, leaving at most **one ≤200-char durable tell**. 🔴 A date in a heading is NOT this verdict.
- **DEMOTE_TO_REFERENCE** — long-but-real procedures, tables, gotcha-depth → `reference/<topic>.md` beside the skill, with **ONE routing line** left in the core ("load it when…").
- **DROP_REDUNDANT** — already elsewhere. 🔴 **A one-shot "cite where" is NOT evidence enough to delete** — run §7's survival check *against the destination* first, with a numbers population and a hide-the-destination control.
- **MERGE_DUP** — the same gotcha restated across N appended sections → one statement.
- **KEEP_HOT** — the core: orientation, the hot commands, the 🔴 safety gotchas.

Bias aggressively toward EVICT/DEMOTE/DROP. A skill is a router, not an archive: a block that matters for ONE task belongs behind a pointer.

🔴 **Read `~/.claude/skills/prune-skill/reference/classification-rules.md` before your first split** — what cites INTO the skill (and by NUMBER, which no path gate sees), why frozen numbers are an API, no COUNT in a routing line, the heading words that make a later pass delete your pointer, and the prune-subagent worktree rules.

## 4. 🔴 Routing paths — get these wrong and the core routes to files nobody can open
A reference file is reached **BY PATH from the core**, and the correct form depends on deployment:

- 🔴 **A bare `reference/<topic>.md` is resolved against the reader's CWD, not the skill's own directory, and is simply NOT FOUND** — measured in both repos; the defect a prune CAUSES most often (188 talos, 47 devrc). **Repo-local skill → `.claude/skills/<name>/reference/<topic>.md`. devrc skill → `~/.claude/skills/<name>/reference/<topic>.md`** (the deployed path).
- 🔴 **EXCEPT `mkOutOfStoreSymlink` skills (`browser`, `dl-router`): only `SKILL.md` + the CLI link ships, so a `~/.claude/…` sidecar path resolves to nothing — point at the SOURCE tree, and LOOK before you write it, because their sidecars are different shapes** (`~/workspace/devrc/scripts/browser-bridge/reference/<topic>.md` is a dir; `~/workspace/devrc/scripts/dl-router/README.md` is one README). **No gate can see this**, so this line is the only guard — and it was itself wrong until 2026-08-27, inventing a `dl-router/reference/` that never existed. This skill's own *"an EXAMPLE earns its own re-measurement"* rule (§7's protocol), missed in the paragraph that says nothing else will catch it.
- 🔴 **Shipping and resolving are separate failures** — whether the file exists, versus whether the reader can resolve the string you wrote. Gated in both repos, and the gate fires even when the file EXISTS. Full table + the `<var>`/`$VAR` conventions: `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md`.

## 5. Execute one atomic rewrite
🔴 **Build every sidecar by VERBATIM LINE-RANGE SLICING of the original — a python slice, never retyping and never re-ordering inside a block.** That makes content survival *structural* instead of something you have to trust, and it is what §7's gap audit checks. Then hand-write the lean core; then verify over the union.

🔴 **The loss mode slicing does NOT cover: a block you SUMMARISE into the core and slice into no sidecar is silently gone — and it looks like good pruning.** That is what §7's gap audit is for. Happened twice.

🔴 **And the loss mode slicing CAUSES: a warning demoted AWAY from the instruction it guards.** Content survives, paths resolve, gates pass — the PAIRING broke. **Ask what each moved block was PROTECTING; if its trigger stays in the core, the guard goes with it or leaves a cross-reference.** Case + the staleness amplifier: `~/.claude/skills/prune-skill/reference/verification-protocol.md`.

Write evicted history to a dated `claudedocs/` doc. **Do not delete a reference file to save bytes** — it saves 0 until opened. An **ORPHAN is usually a demoted topic that lost its routing line — adopt it by default** (one skill held 40 KB across three). Delete only after reading it, and say in the commit why it is dead.

## 6. Land the change
🔴 **Never `git stash`** — `refs/stash` is repo-GLOBAL and shared across every worktree. **Never `git add -A`** — stage explicit paths. 🔴 **A push is not a saving: a body loads from the DEPLOYED copy**, so `readlink -f` it and re-measure there, never `wc -c` in the clone.

Per-repo workflow (worktree recipes, the deployed-path branch, the ff-merge blocker table): `~/.claude/skills/prune-skill/reference/always-loaded-and-landing.md`.

## 7. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py "$SKILL_DIR"
```
**Structural**: under target, every routing path resolves, no orphans, no unclosed fences, numbered-corpus integrity intact.

🔴 **A structural PASS is not content survival — this bar has already missed two real losses.** **Read `~/.claude/skills/prune-skill/reference/verification-protocol.md` and run all six (§1–§5, §7)**, none of which the auditor performs. The one most often got wrong: 🔴 **a control must mutate a DESTINATION, never your new text** — *a control that passes is an invalid control, not a clean result*.

🔴 **Open one demoted file and one routing line by hand before calling it done.** `skill-audit.py` resolves routing paths against the skill directory — which always succeeds — so its ✓ is not evidence a reader following the core from a repo root finds the file. That is the §4 trap; the audit cannot see it.

🔴 **None of the above proves the core is USABLE** — only that content is present. If the skill matters, drive it once on a real task afterwards.

Pair: the `prune-memory` skill (the same cut on the per-session `MEMORY.md` index). 🔴 **Once a skill is lean, add a byte-cap test** — prose budgets do not hold.
