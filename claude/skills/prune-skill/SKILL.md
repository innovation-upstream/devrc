---
name: prune-skill
description: "Audit and aggressively shrink a bloated SKILL.md body so it stops displacing the task it was loaded for. Runs scripts/skill-audit.py, demotes detail to reference/, evicts dated history, rewrites atomically, re-measures. Use when asked to prune/shrink/trim a skill, when a skill body is huge, or when its reference paths have rotted."
argument-hint: "[SKILL_DIR | path/to/SKILL.md] — optional; defaults to $PWD/.claude/skills"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Agent
---

# prune-skill — audit & aggressively shrink a bloated `SKILL.md` body

Goal: stop a skill body from displacing the work it was loaded for. Audit a skill (or a whole `.claude/skills/` dir), then apply the `prune-memory` skill's cut methodology one level down.

Why this recurs: a `SKILL.md` costs **0 tokens until its trigger fires, then ALL of it at once**, so nothing applies per-session pressure and every session appends — one corpus reached 3.13 MB across 67 skills, worst case **742 KB ≈ 186k tokens**, which does not fit a 200k context. That the cost lands per *invocation* rather than per session is no licence to grow: a body that crowds out the task is the same failure in another currency.

**Reference topics** — `reference/<topic>.md` at `~/.claude/skills/prune-skill/reference/`, source `~/workspace/devrc/claude/skills/prune-skill/reference/`:

| Load when | File |
|---|---|
| Before running the staleness pass (§0) — axes + the false-finding traps | `reference/staleness-pass.md` |
| Before verifying (§7) — survival check, gap audit, control design | `reference/verification-protocol.md` |
| Pruning an ALWAYS-LOADED file; landing + re-sync; dispatching prune subagents | `reference/always-loaded-and-landing.md` |

## Budgets (the contract)
- Core **target 12 KB (12,288 B)**, **hard cap 40 KB (40,960 B)**. Past the cap a body is ~10k tokens before any work starts.
- 12 KB is **proven, not aspirational**: `scripts/browser-bridge/SKILL.md` routes a 21-op CLI and 11 reference topics (~113 KB) from **11,845 B**, enforced by its `tests/test_skill_size.py`. In one 9-skill campaign, **8 of 9 landed under target unaided** (mean core 8,576 B). If a complex tool fits, a runbook does.
- **Reference files and `claudedocs/` docs cost 0 until opened** — verified: invoking a skill loads ONLY `SKILL.md`, not a sidecar mentioned by path. Demotion is real token savings, not text-shuffling.
- 🔴 **Landing OVER target is allowed once, deliberately, and never by drift** — only when getting under it would cut the core's *routing value itself*. Stop at the smallest defensible size under the hard cap and **record the reason and the number in the commit message**. Rationale: `reference/always-loaded-and-landing.md`.
- 🔴 **ALWAYS-LOADED files (`CLAUDE.md`, `claude/RULES.md`) are a DIFFERENT problem — this playbook does not apply.** No trigger, so a sidecar saves **nothing**; the only levers are **migration into a trigger-gated owner** and **deleting what is wrong**. Migration converts an always-on rule into one that may never fire, so a rule biting people *outside* that subsystem STAYS (lift it out of any table cell first). Correctness outranks bytes: such a pass may legitimately end **larger**. `RULES.md` has its own gate that OWNS its numbers — read them there. Full model, worked cases and the re-sync procedure: `reference/always-loaded-and-landing.md`.

## 0. 🔴 Staleness pass FIRST — a prune preserves rot BY CONSTRUCTION
Verbatim slicing plus a survival check *guarantee* stale content survives intact, and no path gate proves a claim is TRUE. ~6 tool calls. Run it even when §1 says no prune is needed.
- **Deterministic**: live object/node names; helper scripts exist; **cross-repo paths** (no in-repo gate checks those); metric names.
- **Judgement**: every **load-bearing** claim — a limit, a retention figure, a version pin, an arming state, a "this is impossible" — and internal contradictions.
- 🔴 **Expect MORE false findings than real ones (measured ~6:1); confirm each before "fixing" it.** An empty result is not a rename — go to the DEFINING surface, carry a positive control, and suspect your own instrument first. **A documented filter beats a probe.**
- 🔴 **Fix EVERY copy.** A prune multiplies the copies of every claim — core, sidecar, always-loaded file. Grep the corrected token across the whole skill dir; a core saying "X is stale" beside a sidecar still asserting X is worse than either alone.
- A finding needing a DECISION is FLAGGED, not silently fixed. Findings go in the commit message.

Axes and the traps that manufacture false findings: `reference/staleness-pass.md`.

## 1. Audit (deterministic — no edits)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py            # $PWD/.claude/skills
python3 /home/zach/workspace/devrc/scripts/skill-audit.py path/to/SKILL.md
```
Prints: size vs budget per skill (worst first), per-section byte weights (H2, then H3 in the fattest H2), **dated-history blocks + projected saving**, fat lines (>500 B), **reference-file integrity both directions**, **numbered-corpus integrity**, unclosed fences, and a one-line verdict.

If it says "no prune needed", **stop CUTTING** — don't churn the file for bytes. It is a verdict about **BYTES only**: §0's staleness pass is still due, and the 🔴 below still applies.

🔴 **A SMALL over-budget number does not mean a small defect — the auditor measures bytes, only a read finds rot.** One skill audited 144 B over (1.2%), reading as "trim a line"; the real finding was a *"re-apply these"* section with **two of its four entries retired**. Read the fattest sections whatever the byte verdict says.

🔴 **Read the heading TREE before trusting the section weights** — they are a function of it. One skill had five of eleven documented actions filed as H3s *under an incident write-up's H2*, which is most of why that section scored fat.

## 2. Back up first (the cut deletes/rewrites)
```bash
BK=/tmp/skill-prune-$(date +%s); mkdir -p "$BK"; cp -a <SKILL_DIR>/. "$BK"/; echo "backed up to $BK"
```

## 3. Classify every over-budget block (the judgment cut)
🔴 **Before you split, enumerate what cites INTO this skill** — grep the always-loaded files, sibling skills, `scripts/` and `claudedocs/`. Resolve **distinct PATHS, not citation sites** (they differ 2–3×). The path the always-loaded file cites is the one a path gate blocks on. A citation may also be **by number** ("trap 9"), which no path gate can see.

For a big pass, dispatch read-only classifier subagents (one per fat H2) checking each block against the always-loaded files (`CLAUDE.md`, `~/.claude/RULES.md`) and sibling skills:
- **EVICT_HISTORY** — **work-status** narrative (`### Session …`, `Changelog`, "what we shipped") → a `claudedocs/` doc, leaving at most **one ≤200-char durable tell**.
  🔴 **A date in a heading is NOT this verdict.** `## Common silent-failure modes (from the 2026-05-22 audit)` is durable guidance citing a date; evicting it guts the skill. The auditor reports the two separately for that reason — across one 67-skill corpus **one skill held 45 work-status headings and every other skill ZERO**, while carrying 8–17 dated-but-topical ones. Read "dated lessons" as DEMOTE, never eviction, candidates.
- **DEMOTE_TO_REFERENCE** — long-but-real procedures, tables, gotcha-depth → `reference/<topic>.md` beside the skill, with **ONE routing line** left in the core ("load it when…").
  🔴 **If the block is NUMBERED and cited by number, the numbers are an API — FREEZE them.** A split that renumbers each shard 1..n breaks every citation while leaving all PATHS valid, so no path gate can see it (one 9-file split would strand **53**). Keep the original global numbers — non-contiguous inside a shard is correct — say so in a banner on every shard, and never renumber.
  🔴 **Do not put a COUNT in a routing line.** "(18 gotchas)" is a number no gate checks and every append invalidates. Describe what the file is *for*, not how much is in it.
  🔴 **Never put `session`, `history`, `changelog`, `work log`, `what shipped` or `release notes` in a heading you are CREATING** — a later pass classifies it as evictable work-status and deletes the pointer you just made.
- **DROP_REDUNDANT** — already in an always-loaded file or another skill. 🔴 **A one-shot "cite where" is NOT sufficient evidence to delete** — run §7's survival check *against the destination*, with a numbers population and a hide-the-destination control, first.
- **MERGE_DUP** — the same gotcha restated across N appended sections → one statement.
- **KEEP_HOT** — the core: orientation, the hot commands, the 🔴 safety gotchas.

Bias aggressively toward EVICT/DEMOTE/DROP. A skill is a router, not an archive: a block that matters for ONE kind of task belongs behind a pointer.

🔴 **Dispatching prune (not classifier) subagents: one worktree each, ≤2–3 concurrent, PER-AGENT scratch paths, and before pushing assert the siblings' commits are ancestors of your HEAD** — a stale base silently reverts a sibling's prune. Detail: `reference/always-loaded-and-landing.md`.

## 4. 🔴 Routing paths — get these wrong and the core routes to files nobody can open
Reference files are reached **BY PATH from the core**, and the correct form depends on deployment:

| Deployment | Write the path as |
|---|---|
| **devrc skill** (`claude/skills/<name>/`) — `home.file … recursive`, so `reference/` DOES ship | BOTH forms: `` `reference/<topic>.md` `` at `~/.claude/skills/<name>/reference/`, source `~/workspace/devrc/claude/skills/<name>/reference/` |
| **`mkOutOfStoreSymlink` exceptions** (`browser`, `dl-router`) — only `SKILL.md` + the CLI are linked, **not** a `reference/` subtree | repo-absolute: `~/workspace/devrc/scripts/<subsystem>/reference/<topic>.md` |
| **Repo-local skill** (a project's own `.claude/skills/<name>/`) — the whole dir ships | repo-root-relative: `.claude/skills/<name>/reference/<topic>.md`, **or** short table entries with the expansion stated once above the table |

🔴 **A bare `reference/<topic>.md` in a repo-local skill is resolved by the reader against the CWD, not the skill's own directory, and is simply NOT FOUND** (measured). Shipping and resolving are separate failures: whether the file exists at all is the nix question above; this one bites when it does.

The auditor checks both directions, warning on relative paths with no absolute base. Independently: every backticked repo path in a skill should EXIST. Write a variable segment as `<var>` **in prose** and `$VAR` **inside a bash fence** (`<` is a redirect in shell), and mark a cross-repo path as such.

## 5. Execute one atomic rewrite
🔴 **Build every sidecar by VERBATIM LINE-RANGE SLICING of the original — a python slice, never retyping and never re-ordering inside a block.** That makes content survival *structural* instead of something you have to trust, and it is what §7's gap audit checks. Then hand-write the lean core; then verify over the union.

🔴 **The loss mode slicing does NOT cover: a block you SUMMARISE into the core and slice into no sidecar is silently gone — and it looks like good pruning.** That is exactly what §7's gap audit is for. It has happened twice.

Write evicted history to a dated `claudedocs/` doc. **Do not delete a reference file to save bytes** — it saves 0 until opened. An **ORPHAN is usually a previously-demoted topic that lost its routing line — adopt it into the routing table by default** (one skill held 40 KB across three). Delete only after reading it and saying in the commit why it is dead.

## 6. Land the change (per-repo git workflow)
- **datapacket-talos**: a **throwaway worktree off `origin/trunk`**, never the primary clone (its `CLAUDE.md` rule #10); `git push origin HEAD:trunk`, verify `git show origin/trunk:<file> | head`.
- **devrc**: feature branch + PR against `origin/main`.
- 🔴 **Never `git stash`** in either — `refs/stash` is repo-GLOBAL and shared across every worktree. **Never `git add -A`** — stage explicit paths.
- 🔴 **A push is not a saving — skill bodies load from the CLONE.** After five pushed prunes one primary clone was 160 commits behind, still serving the 92,270 B body. Re-sync (`fetch && merge --ff-only`) and re-measure `wc -c` **in that clone**. Expect the ff-merge to refuse on other sessions' in-flight docs; classify each blocker before touching it — procedure in `reference/always-loaded-and-landing.md`.

## 7. Verify (don't trust — measure)
```bash
python3 /home/zach/workspace/devrc/scripts/skill-audit.py <SKILL_DIR>
```
**Structural**: under target (at least under the hard cap), **every routing path resolves**, no orphans, no unclosed fences, and — if you split a numbered corpus — **`numbered-corpus integrity` reports every citation resolving with no duplicates**.

🔴 **A structural PASS is not content survival — this bar has already missed two real losses.** Also required, every time, per `reference/verification-protocol.md`:
- **UN-SLICED GAP AUDIT** — union your slice ranges, subtract from `1..EOF`, read what is left. Only frontmatter/H1/intro you deliberately kept may be there.
- **ENUMERATED-POPULATION SURVIVAL CHECK** against core+sidecars, **≥5 populations, one MUST be numbers** — a 4-population check reported "65 of 66 covered"; numbers surfaced 5 real losses.
- **TWO INSTRUMENTS** — whole-line survival is load-bearing; token membership is blind to a dropped row whose tokens are quoted elsewhere (0–2 caught vs 5–10).
- 🔴 **VALIDATE THE CHECKER AGAINST A DESTINATION, NEVER YOUR NEW TEXT** — dropping rows from the freshly-written core exercises nothing and PASSES (three actors made this error in one session). **A control that passes is an invalid control, not a clean result.**
- Take before/after bytes from the **pushed blob** (`git cat-file -s <ref>:<path>`) — on a concurrently-edited file a rebase invalidates your "before".

Report before/after bytes, what moved where, the backup path, and the number each control produced. Protocol + checker: `reference/verification-protocol.md`.

🔴 **Open one demoted file and one routing line by hand before calling it done.** `skill-audit.py` resolves routing paths against the skill directory — which always succeeds — so its ✓ is not evidence that a reader following the core from a repo root will find the file. That is the §4 trap, and the audit cannot see it.

🔴 **None of the above proves the core is USABLE** — only that content is present. If the skill matters, drive it once on a real task afterwards.

Pair: the `prune-memory` skill (the same cut on the per-session `MEMORY.md` index); `test_skill_size.py` above is the precedent — once a skill is lean, a byte-cap test keeps it lean.
