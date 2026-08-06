# The Algorithm, applied to how we work — 2026-06-17

Musk's 5-step algorithm run against **everything we've been doing** across both hosts (main + laptop `10.42.0.100`), grounded in the extracted session corpus, not vibes.

Pipeline: `devrc/scripts/session-analysis/{extract_user_msgs,extract_genesis}.py` on each host → `/tmp/algo_analyze.py` (combined). Regenerable.

---

## Part A — What we've actually been doing (the evidence)

**Corpus:** 8,171 messages across **389 sessions**, 2 hosts (main 5,801 / laptop 2,370). After stripping harness noise: **6,044 genuinely-typed messages**, median **69 chars** — short imperative directives.

**Where the work is (last 14 days, 147 sessions):** civitai prod infra dominates — `datapacket-talos` **100**, `civitai` 12, `homelab-talos` 11, `devrc` 7, the rest scattered (vetr, gpu-fleet, naida). This is overwhelmingly **prod ops on one system**, not greenfield.

**How sessions open:** 45% with a slash command, 55% typed. Top openers: `/investigate-alert` (35), `/check-app` (26), `/kubeclaw-agents` (16), `/investigate-network` (11), `/app-blocks` (10), `/manage-postgres` (10), `/next-lever` (8). Typed openers are dominated by **"read X (load context)" (59)** and **"analyze/recon X" (32)** — i.e. re-loading state and re-deriving subsystem maps.

**~40 distinct slash commands/skills** are in active use. The work is heavily tooled already.

**Rituals still hand-typed (the genesis stems, ≥4×):**
- `read <handoff doc>` continuity-loading — 13+ (plus 59 "read X" openers)
- `dispatch a subagent to audit the PR for risks/regressions…` — ~31 (now `/audit-pr`)
- `proceed use subagent ensure test coverage` — 13 (now a standing CLAUDE.md rule)
- `analyze the X setup, then…` — 32 (now `/analyze-service`)
- `recommend next steps` — 9
- `dispatch a subagent to use playwright to click through…` — 9
- `its been a few days check` / `its the next day check` — 8 (periodic re-check)
- `do we have a claude code skill for this` — 5
- `give me the kickoff message to copy-paste` — 5 (now `/handoff`)

**Quality is not the problem.** Pure-steering 2.2%, correction/frustration **1.7%**. The work lands. So the Algorithm's leverage here is **deletion of inert process**, not mistake-prevention.

**The governing requirements layer:** `~/.claude/CLAUDE.md` (14 lines, operational) + `PRINCIPLES.md` (60) + `RULES.md` (**331 lines, 170 bullets, 20 sections**) = **405 lines loaded into every session on every host.**

---

## Part B — The Algorithm

### 1. Question every requirement — and name the person

There is no legal/safety department here. **Every requirement traces to one of two people:**

- **Zach** — the operational rules in `CLAUDE.md` (git hygiene, nix-shell, subagent-default, worktree-isolation). These were forged from his own pain and are exercised constantly. *These pass the question.*
- **The SuperClaude framework author** (imported wholesale into `PRINCIPLES.md` + `RULES.md`). This is the dangerous case the Algorithm warns about: a **smart, plausible-sounding external source, adopted un-questioned.** The tell is in the text itself — "SuperClaude framework," "PM Agent meta-layer," "Quality Quadrants," "Session Lifecycle /sc:load→/sc:save."

Cross-checking the imported requirements against 389 sessions of actual behavior:

| Imported requirement (RULES.md) | Prescribed | Actually fired |
|---|---|---|
| PM-Agent self-improvement / "document after every task" | top-of-file section | **0×** |
| `/sc:load → Checkpoint(30min) → /sc:save` lifecycle | mandated | `/sc:save` **0×**, `/sc:load` 2× |
| TodoWrite for >3-step tasks | mandated | user-invoked **0×** |
| "specify expected parallelization gains (e.g. 60% time saving)" | mandated | never — and it **directly contradicts** the same file's "No Fake Metrics" rule |
| SOLID / Systems-Thinking / Decision-Framework (PRINCIPLES.md) | foundational | never referenced in any actual task |

**Verdict:** a large fraction of the 405 governing lines are inert — adopted because the source sounded authoritative, never load-bearing. Smart-person requirements, unquestioned. The few requirements that *are* dangerous-and-followed (the prescriptive PM-Agent/lifecycle ritual) are dangerous precisely because they'd add ceremony with zero observed payoff.

### 2. Delete (expect to add ≥10% back)

**Delete from the requirements layer** (target: 405 → ~120 lines):
- The **PM-Agent / Agent-Orchestration self-improvement** section (0 fires).
- The **`/sc:` session lifecycle** (load/checkpoint/save) prose (0 saves).
- The **TodoWrite mandate** and **"parallelization-gain estimate"** requirement (latter is self-contradictory).
- Most of **`PRINCIPLES.md`** (SOLID/Quadrants/Decision-Framework — generic, inert).
- The **"Quick Reference & Decision Trees"** appendix (restates rules already stated above it).
- Redundant restatements of "use the best tool / parallelize / batch" scattered across Tool-Optimization + Workflow + Planning.

**Delete from the process layer:**
- Any command used **1×** that isn't a deliberate rarely-but-critical runbook (audit the long tail of single-use commands; several are abandoned experiments).
- `/sc:load` / `/sc:save` commands themselves if the lifecycle prose is deleted.

**The ≥10% add-back (the rules earned by real pain — keep, they fire constantly):**
- **Token & Tool Hygiene** (Write-over-heredoc, Read-before-Edit, don't-re-read) — derived from real audits.
- **Verification Honesty** (deployed≠verified, reproduce the symptom) — earned from real misses; matches the 9× hand-typed Playwright-verify ritual.
- **Memory-is-a-Hypothesis** + **Deterministic-over-Prose** — earned, and both got exercised *this very session* (stale laptop IP; live-discovery over frozen map).
- **Git rules** (never add -A / reset --hard / the rebase recipe) + **NixOS** + **subagent/test/PR default** + **worktree isolation**.

If after cutting you've kept more than ~120 lines, you didn't cut enough.

### 3. Simplify & optimize (only what survived deletion)

- The surviving rules are already terse — collapse the duplicate "use best tool/parallel" rules into **one** Tool-Optimization rule.
- **Do NOT over-consolidate the command fleet.** The `investigate-*` (4) and `manage-*` (8) families look like duplication but `AGENTIC_LEVERAGE.md`'s skill-granularity rule already governs this deliberately (runbook depth ⇒ own skill). Respect it; this is a case where apparent duplication is correct.
- Simplify continuity: 59 "read X" + 13 "read handoff" openers mean **context-reload is the single most repeated act.** `/resume` now collapses it — but it's unproven (see §5).

### 4. Accelerate cycle time (only now)

The dominant cycle is **PR → audit → deploy → verify**. Two accelerable, un-automated steps remain:
- **Verify** is still hand-typed Playwright (9×). The `/verify` skill exists — wire it as the default closer so "deployed" auto-advances to "reproduced the click-path."
- The **periodic re-check** ("its been a few days check", 8×) is a latency gap — a human remembering to look. This is a scheduling problem, not a typing problem.

### 5. Automate (last — and note we already broke this rule)

The Algorithm's sharpest point lands on us: **~40 commands were built before the requirements were questioned or the dead process deleted.** Some commands automate processes that step 1–2 say shouldn't exist (maintaining `/sc:load`/`/sc:save` machinery for a lifecycle that fires ~0×). That is the "automated too early" mistake, in our own repo.

So automate **only what survived all four steps:**
- **`/verify` as the cycle closer** (step 4) — high-frequency, real.
- The **periodic-recheck** ritual → `/schedule` or `/loop`, *if* it survives questioning ("does a few-days drift check actually need to exist, or should the relevant sweeps page us?" — note `dr-sweep`/`cert-sweep`/`capacity-sweep` already exist; the manual check may be **deletable**, not automatable).
- **Stop building** until the requirements layer is cut. New commands on top of 405 lines of mostly-inert rules is more harness, not more leverage — the exact failure mode `close-the-loop`/`STATE.md` already warns about.

---

## The one-line conclusion

We have a **low-error (1.7%), heavily-tooled, prod-ops-dominated** practice whose biggest waste is not mistakes but **inert ceremony**: 405 lines of governing requirements (most imported un-questioned and never fired) and a command fleet partly built ahead of the deletion step. The highest-leverage move is **subtractive** — question the imported requirements by name, delete ~70% of the rules layer, keep the ~10% earned by real pain, then automate only the PR→verify closer. Build less; delete more.

---

### Appendix — reproduce
```bash
# on each host:
python3 ~/workspace/devrc/scripts/session-analysis/extract_user_msgs.py /tmp/msgs_<host>.jsonl
python3 ~/workspace/devrc/scripts/session-analysis/extract_genesis.py   /tmp/genesis_<host>.jsonl
# combine + analyze:
python3 /tmp/algo_analyze.py   # (scp laptop files back first)
```
