---
name: close-the-loop
description: "Decide what to automate next and SHIP it as a closed loop — score candidates, enforce the bounded-work + cheap-verifier gate, validate over real runs. Use when deciding what to automate next, when an agentic effort feels like \"more harness but no value\", or when asked to close or validate a loop."
argument-hint: "<action> — run (full pass, default) | aim (just pick the next loop) | validate (drive an in-flight loop to closed) | capture (write a lesson/decision back to STATE.md)"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion, WebSearch, WebFetch
---

# Close the loop

The harness is built. The recurring failure is **improving the harness instead of shipping a
loop that creates value** ("sharpening the saw, never cutting wood"). Pick one loop, point the
existing harness at it, drive it to *closed + validated* on real work, capture what was learned.

**Always start by reading `STATE.md`** (next to this file) — the living ledger: north-star,
validated lessons (do not relearn them), harness inventory, loop ledger (shipped / closing /
next / demoted). **Always update `STATE.md` at the end** of a run (new lesson, ledger row,
decision). It is the compound-engineering memory; skipping the write-back is the cardinal sin.

🔴 **The write-back only works because `STATE.md` and `ARCHIVE.md` are the DELIBERATE
mkOutOfStoreSymlink exception in this skill dir.** Every other file here is a read-only
/nix/store symlink; until 2026-08-10 these two were as well, so the "update it last" contract
had been silently **inert** — writes could not land. They now point at the live checkout:

| deployed (write here) | real file (the source of truth) |
|---|---|
| `~/.claude/skills/close-the-loop/STATE.md` | `~/workspace/devrc/claudedocs/close-the-loop/STATE.md` |
| `~/.claude/skills/close-the-loop/ARCHIVE.md` | `~/workspace/devrc/claudedocs/close-the-loop/ARCHIVE.md` |

Two consequences. **(1)** A write applies immediately — no `home-manager switch` needed.
**(2)** It leaves an UNCOMMITTED change in the devrc working tree: **commit it in the same
session** (RULES.md → "Docs/notes written into a working tree are UNSAVED WORK"), on a branch,
never on `main`. If a write is ever refused as read-only, the symlink has regressed to a store
path — check `readlink -f`, do not work around it by writing somewhere else.

---

## The hard gate (validated, non-negotiable)

A loop will only **close** if BOTH hold. Score every candidate against them first; reject
anything that fails.

1. **Bounded work-unit** — one PR, one alert, one service's config, one node. NOT open-ended
   exploration.
2. **Cheap *automatic* verifier** — a mechanical yes/no that fires without a human judging an
   LLM: *tests pass · alert clears · config assertion · diff empty · threshold crossed ·
   restore checksum matches · pod Ready*. A human judging an LLM's open-ended output is
   **not** a cheap verifier.

**Evidence this is real:** the `perf-deep-dive` runbook (open-ended LLM analysis + human-judgment
verifier) failed to close **2/2 real runs** (context overflow, then silent model timeout) — the
dogfood only "passed" on a smaller rigged baseline. Open-ended-analysis-as-a-loop does not
converge. It stays a *manual tool*, never a loop.

---

## Aim selection — where to point it (the heart of the problem)

The bottleneck is never capability; it's **aim**. Score candidates on two axes, pick upper-right:

- **Verifier cost** (cheaper = better) — see the gate above.
- **Revenue / time proximity** (fewer hops to money or to Zach's reclaimed hours = better).

**Question-generation is the front-end of aim-selection — and a well-posed question is the
antidote to the open-ended-analysis trap.** "Go analyze X" fails the gate (unbounded, no clean
verifier — see perf-deep-dive). "Answer THIS specific question from THIS data" passes it: the
question *is* the boundary, and its answer is checkable. So before/within aiming, go to a real
data corpus (metrics, events, cluster state, this ledger) and surface the most decision-relevant
questions — **of the data** ("why did X happen?") and **of ourselves** ("what are we assuming
that we haven't checked?"). Answer the bounded ones; rate which were *worth asking* — a cheap
operator up/down, captured **by hand into STATE.md** (there is no automatic capture substrate;
see step 6). Improving question quality compounds results faster
than improving answers, and is the highest-leverage loop candidate (see STATE.md ledger) because
it scales Zach's judgment — his actual bottleneck.

Rules that override naive scoring:
- **The infra-hygiene quadrant is drained** (cheap verifier, 3+ hops from value). Do NOT default
  there — *except* where an infra loop is the **unlock** that lets Zach safely leave infra for
  product (durability/saturation guards → maintenance mode → freed hours → civitai).
  Infra-as-unlock is in scope; infra-as-busywork is not.
- **Follow where Zach's *judgment* is the bottleneck**, not his hands. He is the bottleneck on
  *deciding what to build* and *hard technical problems* — not execution or customers. Aim
  agents at *generating well-formed options for his judgment* and at *absorbing the work that
  crowds out his deep focus* — not at replacing the judgment.
- **Portfolio allocation:** the north-star is civitai product/revenue (App Blocks). Infra
  (`datapacket-talos`, `civitai-gpu-fleet`) is going to maintenance mode. Loops that free Zach
  toward civitai, or move civitai directly, beat loops that don't. (See STATE.md north-star.)
- **"Instrumented-but-unread":** having observability ≠ closing the loop. If a check exists but
  its finding went unread/unrouted, the fix is *routing/action*, not a new checker. Diagnose
  coverage-gap vs closing-gap before building.

---

## The process

1. **Elicit / confirm the value** (`aim` or full `run`). If the target is unclear, interview
   (AskUserQuestion) for: what's it in service of, where the time/judgment leaks, what would be
   different in a month. Don't presume; the value is usually not where the recent commit/alert
   mining points (that's recency-biased toil).
2. **Enumerate candidate loops**, each with: work-unit, trigger, the *automatic* verifier, blast
   radius, build effort, and whether real input exists *now* to validate on.
3. **Gate + score** — drop anything failing the hard gate; rank survivors on verifier-cost ×
   revenue/time-proximity. State the standing tension (cheapest-verifier vs highest-value) when
   they disagree.
4. **Ship the chosen loop** through the existing harness (clawgate runbook / scheduled agent /
   gate). Bounded blast: agent proposes, human/GitOps applies; mutations stay checkpoint-gated.
   Reuse, don't rebuild (check the fleet inventory in STATE.md + the live cluster).
5. **Validate it CLOSES** — the bar: runs on **real un-rigged work**, the **verifier fires
   automatically**, the output is **acted on**, demonstrated across **≥3 real runs** with a
   measured signal (false-positive rate / acceptance rate / did-the-win-hold). One rigged demo
   is not validation.
6. **Capture (compound)** — write the lesson/decision back to STATE.md; where possible encode
   the correction as a rule/runbook so the mistake can't recur.
   ⛔ **There is no decision-labeling substrate any more.** The old loop (0.6.4: approve/deny →
   `decision_labels` + a per-proposal metric) was **REMOVED in clawgate 0.7.18–0.7.24; migration
   0011 drops the table** — it captured per-turn self-approvals, not judgments. Do NOT go looking
   for it. What exists today is **`clawgate_permission_decisions_total{outcome}`** (+
   `clawgate_permission_decision_latency_seconds`) in Prometheus and the Faro **`permission.action`**
   event in Loki — see `~/.claude/skills/clawgate/reference/telemetry.md` (source
   `~/workspace/devrc/claude/skills/clawgate/reference/telemetry.md`).
   ⚠ Those measure a **~4k/day, ~97% auto-approved firehose**, so treat them as a *usage* signal,
   NOT as an acceptance-rate that can retire a checkpoint. The graduated-autonomy signal the old
   loop was supposed to provide **does not currently exist** — per the validated lesson in STATE.md
   it must be rebuilt on *checkpoint* decisions over *runbook-dispatched* work (scarce, attributable),
   which only becomes possible once such runbooks run at volume.

---

## Anti-patterns (the traps that have already bitten)

- **Open-ended-analysis-as-a-loop** — fails the gate; stays a manual tool (see perf-deep-dive).
- **Sharpening the saw** — building/polishing the harness instead of shipping a loop. Capturing
  the method (even this skill) must not become the work; its first act is to close a real loop.
- **Instrumented-but-unread** — green traces, missed outcomes (lost Redis data despite
  observability). The missing piece is the *outcome verifier + routing*, not more dispatch.
- **Workslop** — a loop whose output needs Zach's unverified review is negative-value for a solo
  operator (he IS the downstream reviewer). If you can't name the automatic verifier, don't aim
  there yet.
- **Mining recent toil** — surfaces only "automate what you just did," and overlaps what's
  already shipped. Aim from value + verifier, not from the last 14 days of activity.
- **Routing an unverified finding** — the order is **surface → VERIFY → route**, NOT surface →
  route. A question-run *surfaces* a candidate (a suspicion); a verify-run (type C /
  outcome-verification) checks it against ground truth *before* anything is routed to an actor.
  This bit us: a B-run "viewer-consent gap" got posted to a PR as an `[ACTION]` before a C-run
  traced the code and found it was already mitigated (a false positive a feature agent might
  have built redundant work on). **Run types check each other** — B finds candidates, C audits
  *both* false-positives *and* false-negatives (the same C-run found the real open hole B
  couldn't see). Never route a suspicion as a decision.

## Pointers
- `STATE.md` (this dir, writable — source `devrc/claudedocs/close-the-loop/STATE.md`) — the
  living ledger; read first, update last.
- `ARCHIVE.md` (same) — shipped narrative and superseded decisions; **not** read on every run.
- `/clawgate` skill — operate the harness (status, deploy, runbooks, logs).
- Memories: `clawgate-loop-validation` (the perf-deep-dive failure + the gate),
  `clawgate-phase3` / `clawgate-runbooks` (harness capability).
