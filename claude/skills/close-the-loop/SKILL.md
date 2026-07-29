---
name: close-the-loop
description: Find and SHIP the next high-leverage *closed* agentic loop for Zach's systems — elicit the real value, score candidates on verifier-cost × revenue/time-proximity, enforce the bounded-work + cheap-automatic-verifier gate, ship + validate over real runs, and capture the decision so it compounds. Use when deciding what to automate next, when an agentic effort feels like "more harness but no value," when asked to close/validate a loop, or to run the loop-closing exercise. The harness (clawgate: dispatch/runbooks/checkpoints/privilege/decision-labeling) is already built — this skill is about pointing it at something that creates value and CLOSES. Reads + updates STATE.md (the living ledger) every run.
argument-hint: "<action> — run (full pass, default) | aim (just pick the next loop) | validate (drive an in-flight loop to closed) | capture (write a lesson/decision back to STATE.md)"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion, WebSearch, WebFetch
---

# Close the loop

The harness is built. The recurring failure is **improving the harness instead of shipping a loop that creates value** ("sharpening the saw, never cutting wood"). This skill exists to break that: pick one loop, point the existing harness at it, and drive it to *closed + validated* on real work — then capture what was learned so the next one is cheaper.

**Always start by reading `STATE.md`** (next to this file) — the living ledger: the north-star, the validated lessons (do not relearn them), the harness inventory, and the loop ledger (shipped / closing / next / demoted). **Always update `STATE.md` at the end** of a run (new lesson, ledger row, decision). It is the compound-engineering memory; skipping the write-back is the cardinal sin.

---

## The hard gate (validated, non-negotiable)

A loop will only **close** if BOTH hold. Score every candidate against them first; reject anything that fails.

1. **Bounded work-unit** — one PR, one alert, one service's config, one node. NOT open-ended exploration.
2. **Cheap *automatic* verifier** — a mechanical yes/no that fires without a human judging an LLM: *tests pass · alert clears · config assertion · diff empty · threshold crossed · restore checksum matches · pod Ready*. A human judging an LLM's open-ended output is **not** a cheap verifier.

**Evidence this is real:** the `perf-deep-dive` runbook (open-ended LLM analysis + human-judgment verifier) failed to close **2/2 real runs** (context overflow, then silent model timeout) — the dogfood only "passed" on a smaller rigged baseline. Open-ended-analysis-as-a-loop does not converge. It stays a *manual tool*, never a loop.

---

## Aim selection — where to point it (the heart of the problem)

The bottleneck is never capability; it's **aim**. Score candidates on two axes and pick the upper-right:

- **Verifier cost** (cheaper = better) — see the gate above.
- **Revenue / time proximity** (fewer hops to money or to Zach's reclaimed hours = better).

**Question-generation is the front-end of aim-selection — and a well-posed question is the antidote to the open-ended-analysis trap.** "Go analyze X" fails the gate (unbounded, no clean verifier — see perf-deep-dive). "Answer THIS specific question from THIS data" passes it: the question *is* the boundary, and its answer is checkable. So before/within aiming, go to where the action is (a real data corpus — metrics, events, cluster state, this ledger) and surface the most decision-relevant questions — **of the data** ("why did X happen?") and **of ourselves** ("what are we assuming that we haven't checked?"). Answer the bounded ones; rate which were *worth asking* (cheap up/down → decision-labeling → compound). Improving question quality compounds results faster than improving answers. This is itself a loop candidate (see STATE.md ledger) and the highest-leverage one, because it scales Zach's judgment — his actual bottleneck.

Rules that override naive scoring:
- **The infra-hygiene quadrant is drained** (cheap verifier, but 3+ hops from value). Do NOT default there — *except* where an infra loop is the **unlock** that lets Zach safely leave infra for product (durability/saturation guards → maintenance mode → freed hours → civitai). Infra-as-unlock is in scope; infra-as-busywork is not.
- **Follow where Zach's *judgment* is the bottleneck**, not his hands. He is the bottleneck on *deciding what to build* and *hard technical problems* — not execution or customers. Aim agents at *generating well-formed options for his judgment* and at *absorbing the work that crowds out his deep focus* — not at replacing the judgment.
- **Portfolio allocation:** the north-star is civitai product/revenue (App Blocks). Infra (`datapacket-talos`, `civitai-gpu-fleet`) is going to maintenance mode. Loops that free Zach toward civitai, or that move civitai directly, beat loops that don't. (See STATE.md north-star.)
- **"Instrumented-but-unread":** having observability ≠ closing the loop. If a check already exists but its finding went unread/unrouted, the fix is *routing/action*, not a new checker. Diagnose coverage-gap vs closing-gap before building.

---

## The process

1. **Elicit / confirm the value** (`aim` or full `run`). If the target is unclear, interview (AskUserQuestion) for: what's it in service of, where the time/judgment leaks, what would be different in a month. Don't presume; the value is usually not where the recent commit/alert mining points (that's recency-biased toil).
2. **Enumerate candidate loops**, each with: work-unit, trigger, the *automatic* verifier, blast radius, build effort, and whether real input exists *now* to validate on.
3. **Gate + score** — drop anything failing the hard gate; rank survivors on verifier-cost × revenue/time-proximity. State the standing tension (cheapest-verifier vs highest-value) when they disagree.
4. **Ship the chosen loop** through the existing harness (clawgate runbook / scheduled agent / gate). Bounded blast: agent proposes, human/GitOps applies; mutations stay checkpoint-gated. Reuse, don't rebuild (check the fleet inventory in STATE.md + the live cluster).
5. **Validate it CLOSES** — the bar: runs on **real un-rigged work**, the **verifier fires automatically**, the output is **acted on**, demonstrated across **≥3 real runs** with a measured signal (false-positive rate / acceptance rate / did-the-win-hold). One rigged demo is not validation.
6. **Capture (compound)** — write the lesson/decision back to STATE.md; where possible, encode the correction as a rule/runbook so the mistake can't recur. The clawgate **decision-labeling loop** (0.6.4: approve/deny → `decision_labels` + `clawgate_proposal_decisions_total`) is the capture substrate — its acceptance-rate is the graduated-autonomy signal (≥90% over ≥N → candidate to drop the checkpoint).

---

## Anti-patterns (the traps that have already bitten)

- **Open-ended-analysis-as-a-loop** — fails the gate; stays a manual tool (see perf-deep-dive).
- **Sharpening the saw** — building/polishing the harness instead of shipping a loop. Capturing the method (even this skill) must not become the work; its first act is to close a real loop.
- **Instrumented-but-unread** — green traces, missed outcomes (lost Redis data despite observability). The missing piece is the *outcome verifier + routing*, not more dispatch.
- **Workslop** — a loop whose output needs Zach's unverified review is negative-value for a solo operator (he IS the downstream reviewer). If you can't name the automatic verifier, don't aim there yet.
- **Mining recent toil** — surfaces only "automate what you just did," and overlaps what's already shipped. Aim from value + verifier, not from the last 14 days of activity.
- **Routing an unverified finding** — the order is **surface → VERIFY → route**, NOT surface → route. A question-run *surfaces* a candidate (a suspicion); a verify-run (type C / outcome-verification) checks it against ground truth *before* anything is routed to an actor. This bit us: a B-run "viewer-consent gap" got posted to a PR as an `[ACTION]` before a C-run traced the code and found it was already mitigated (a false positive a feature agent might have built redundant work on). **Run types check each other** — B finds candidates, C audits *both* false-positives *and* false-negatives (the same C-run found the real open hole B couldn't see). Never route a suspicion as a decision.

## Pointers
- `STATE.md` (this dir) — the living ledger; read first, update last.
- `/clawgate` skill — operate the harness (status, deploy, runbooks, logs).
- Memories: `clawgate-loop-validation` (the perf-deep-dive failure + the gate), `clawgate-phase3` / `clawgate-runbooks` (harness capability).
