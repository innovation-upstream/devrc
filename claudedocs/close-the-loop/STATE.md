# close-the-loop — living state / ledger

_The evolving memory the `close-the-loop` skill reads first and updates last._

**Last updated: 2026-08-01** (restructured: shipped narrative → `ARCHIVE.md`; the stale
decision-labeling substrate corrected throughout). **Last substantive state: 2026-07-05.**

> 📁 **History lives in `ARCHIVE.md`** (next to this file) — shipped-and-verified narrative,
> build history, superseded decisions, and the full-evidence ledger rows. This file carries only
> what is **live**: what is open, the decisions that still bind, and the rules the skill applies.
> **Rules that still bind stayed HERE together with their reasoning** — a rule stripped to a
> status line is a rule someone re-derives wrongly later.

**Every run: read this file first, update it last.** Skipping the write-back is the cardinal sin.
Put *status* in the ledger row or in `ARCHIVE.md`; put *lessons/decisions* in the two sections
below that carry them.

---

## ▶ START HERE — current state + next move

**The harness is DONE. The discipline is: STOP building loop features; USE what exists.**

**Where things stand (2026-07-05):** the clawgate **agent loop CLOSES end-to-end** — Task →
Dispatch → agent → **real PR** → `ready_for_review`, soaked 2026-07-03→05 over ~7 real tasks
(#58–#64), each producing a genuine PR. Hardening shipped so the soak does not rot (idle-task
reaper, live dismiss regroup, structured transcript). **The mechanism is proven.**

**The open question is ADOPTION / RELEVANCE of what gets dispatched — not the mechanism.**
That has been the binding constraint for three separate threads now (drafter, Suggestions,
Tasks queue), and it is *never* fixed by building more harness.

**Live next moves (pick from here, do not invent harness):**
1. **Use the loop on real work** — dispatch something that matters and let the PR be the verifier.
2. **Relevance before workflow** — when an adoption loop will not close, check whether the ITEMS
   are Zach's before concluding the workflow is wrong (this was the drafter's actual root cause:
   ~90% of cards were other people's tickets; fixed with an assignee filter).
3. **repo-cos**: BUILD IS DONE. Watch real weekly cycles. Do **not** add features.
4. **Parked, needs operator go-ahead:** the `nats` JetStream backup gap (the one real durability
   gap), the context-capturing durability guard, the node-saturation guard.
5. **Open hygiene:** rotate the OpenRouter key (`~/.config/repo-cos/env` + it appears in
   transcripts) — operator previously declined; still outstanding.

_Full narrative for every thread above: `ARCHIVE.md`._

---

## Standing decisions (STILL BINDING — with the reasoning, not just the verdict)

These constrain future choices even though the work that produced them shipped. Archiving these
as "history" is how they get re-derived wrongly.

- 🔴 **GATE EVERY PROD MUTATION** (operator, 2026-06-09). Read-only reconcilers run silent; ANY
  action changing prod needs a checkpoint until that action *class* earns autonomy.
  **Reasoning: blast-radius does NOT auto-grant trust — trust HISTORY does.** The "auto-act on
  low-blast" option was explicitly rejected. ⚠ The acceptance-rate feed that was supposed to
  retire action gates **no longer exists** (see the decision-labeling lesson) — so today, nothing
  graduates automatically.
- 🔴 **The bottleneck is NEW-TASK SPECIFICATION, not execution** (operator, 2026-06-23).
  **Reasoning:** execution + approval are already solved by clawgate auto-approve — most tasks
  already run autonomously. Toil-elimination is *subtraction* and caps near ~1.2x (zeroing all his
  typing ≈ 1 hr/day); 10x needs a *multiplier*. So: **do not aim at seconds-savers.** Aim at the
  rate of well-specified work flowing INTO the fleet.
- 🔴 **Aim where the VERIFIER IS THE ARTIFACT, not a human decision.** Reasoning, measured twice:
  the QA/UX-audit and mail-actions loops CLOSED because a broken view or a missing invoice is
  self-evident; the drafter and Suggestions did NOT close because their verifier was Zach's
  adjudication = adoption-gated. If you cannot name the automatic verifier, do not aim there.
- 🔴 **Opt-in fails for HUMAN ritual; it is the FAVORED pattern for an AGENT rail.** Reasoning:
  measured — the audit ritual stayed hand-typed ~51x/7d while `/audit-pr` sat unused, BUT agents
  *do* reach for `/audit-pr`, `/handoff` and `ship.sh`. **The yardstick is "used by human OR
  agent."** ⚠ Do not re-reject a good agent rail by citing "opt-in doesn't stick" — that mistake
  was made once already and the operator corrected it.
- 🔴 **Small compounding agent-rail wins run ALONGSIDE the 10x lever** — they do not compete for
  it. ("Think bigger / no more toil-removal" was too absolute; operator correction.)
- **Do NOT build more standalone Suggestions polish** (full-transcript scroll-back, Web Push).
  Reasoning: measured **0 engagement**; the plan is to fold it into the Tasks queue as a
  generative source, or cut it — not to iterate it standalone.
- **Model/cost:** the drafter's canonical impl is the **homelab kubeclaw agent on DeepSeek V4
  Pro** (metered OpenRouter); the devrc/Haiku prototype is **RETIRED**. Reasoning: DeepSeek gives
  Opus-tier judgment (it self-selects NEEDS-DECISION on payments/legal risk, which Haiku did not)
  at ~1/20th the cost, AND it runs **off Zach's Claude subscription** — the `claude -p` "$/ticket"
  figures were never a bill, they were list-price equivalents against his *usage quota*.
- **Cheap models break SAFETY, not just quality** — Haiku and Sonnet both confidently mis-drafted
  the safety-critical ticket with no flag. **Cheap out on SCOPE (delta-only), never on judgment.**
- **App Blocks EXECUTION is driven in OTHER sessions — do not duplicate or collide.** This
  thread's lane is the productivity machine + the infra focus-shield.
- **clawgate *app* development is spun out** — this thread depends on it but does not build it.
- 🔴 **Business metrics NEVER go in the public `civitai` repo** — funnel/revenue snapshots go to
  the private `datapacket-talos/claudedocs/`.
- **`surface → VERIFY → route`, never surface → route.** Reasoning: a B-run suspicion was posted
  to a PR as an `[ACTION]` before a C-run traced the code and found it already mitigated — a false
  positive a feature agent could have built redundant work on.

---

## North-star (the goal everything serves)
Zach is **infra lead** for `datapacket-talos` (civit production) + `civitai-gpu-fleet` — his primary responsibility, now **transitioning to maintenance mode**. As that wraps, he is **shifting focus to civitai the product** (the **App Blocks** project is his first major product contribution, ships soon). **Any freed hours go to civitai product + revenue.**

His bottleneck is **judgment + deep technical focus**, NOT execution or customers: he picked "deciding what to build" + "hard technical problems" as the high-value work that gets crowded out, and "spread too thin" as the leak. So loops should either (a) **free him from infra safely** (the maintenance-mode unlock) or (b) **generate well-formed options for his judgment / absorb work that crowds out deep focus** — never try to replace the judgment.

### The deeper goal: compound the quality of the QUESTIONS, not just the answers (2026-06-07)
Zach's framing: *"determining next steps requires going to where the action is and asking the right questions — of the data and of ourselves. If we automate asking + answering better and better questions, we get better results faster."*
- **Key insight (it solves the perf-deep-dive failure):** a *well-posed question is a bounded, verifiable work-unit* — "answer THIS question from THIS data" passes the gate; "go analyze X" doesn't. **Automating question-asking is the antidote to the open-ended-analysis trap, not another instance of it.** The question is the boundary.
- This is the highest-leverage direction because it scales his *judgment* directly (the bottleneck), and it's the **front-end of this very skill** — aim-selection IS question-generation. Better questions → better loops → better results.
- It was originally justified partly as "the loop that finally uses the decision-labeling substrate." ⛔ **That justification is DEAD** — the substrate was removed (clawgate 0.7.18–0.7.24, migration 0011 drops `decision_labels`). The question-quality direction stands on its own merits above; it no longer has, and must not assume, a capture substrate.

## Validated lessons (do NOT relearn)
- **The gate:** a loop closes only with a **bounded work-unit + a cheap *automatic* verifier**. Proven by `perf-deep-dive` failing **2/2 real runs** (context overflow; silent model timeout) — open-ended LLM analysis + human-judgment verifier does not converge. [[clawgate-loop-validation]]
- **Infra-hygiene quadrant is drained** — cheap verifier but low revenue-proximity. Pursue infra ONLY where it's the unlock (durability/saturation → maintenance mode). Generic hygiene (drift/orphans/etc.) is not the value.
- **Instrumented-but-unread is the real gap** — Zach has Prometheus/observability and STILL lost Redis data + had nodes freeze. The missing piece is the **outcome verifier + routing**, not capability. Before building a checker, diagnose coverage-gap vs closing-gap (does a check exist whose finding went unread?). **✅ RE-VALIDATED 2026-06-16:** asked to "schedule `fleet-status.sh`" as automation — diagnosed coverage FIRST instead of building, and found staleness was ALREADY alerted for 10/20 cronjob levers (capacity/reliability×6/dr/cert/reaction-abuse `*Stale` rules) + all deployments (generic `Kube*` rules); a scheduled fleet-status would have been redundant + off-channel + lower-fidelity. The real gap was NARROW — 10 fleet cronjobs with no staleness alert (`KubeJobFailed` catches run-and-fail, not silent-stop). Closed at the right layer (a Grafana warning alert in the existing alerting stack), NOT by adding a parallel checker. The instinct to "schedule the script" is the agent-wash trap; the discipline caught it.
- **Compound engineering** — capture every correction as a rule so it can't recur. The **write-back** (corrections → agent rules) is the highest-leverage compound primitive still open — but build the CAPTURE half deliberately at the right granularity (see next bullet).
- **⛔ The decision-labeling loop was REMOVED (clawgate 0.7.18, 2026-06-11) — it captured the WRONG distribution.** It recorded every approve/deny at permission cards + checkpoints, but live data was **1496 rows / 3 days, 100% `permission`-`approve`, ZERO checkpoints** — i.e. logging Zach reflexively approving his own `kubectl`/`cd` commands across every repo, not genuine judgments. The acceptance-by-runbook table it existed for was **permanently empty**. **Durable lesson: a capture mechanism only yields signal if it captures real, scarce JUDGMENTS at the right granularity — a fire-and-forget per-turn approval is not a judgment.** The graduated-autonomy / write-back vision above must NOT be rebuilt on a "label every approval" substrate; the signal lives in *checkpoint* decisions on *runbook-dispatched* work (scarce, attributable) — which only exist once such runbooks run at volume (they don't yet).
  **✅ RE-VERIFIED 2026-08-01 against the `/clawgate` skill** (`reference/changelog.md` for the removal, `reference/internals.md` for migration 0011, `reference/telemetry.md` for the successor). What exists in its place is **`clawgate_permission_decisions_total{outcome}`** + `clawgate_permission_decision_latency_seconds` (Prometheus) and the Faro **`permission.action`** event (Loki). ⚠ These are a **usage** signal, not an acceptance-rate: the measured reality is a **~4k prompts/day, ~97% auto-approved firehose** — i.e. exactly the per-turn self-approval noise that got the old loop deleted. **Do NOT wire graduated autonomy to them.**
- **STILL-unvalidated candidate loop: clawgate "Suggested next step" (0.7.23, matured 0.7.25→0.7.30).** A `Stop` hook → per-project-opt-in (throttled) OpenRouter suggestion in a Suggestions tab; right gate (cheap per-turn POST, LLM only on flagged-project/on-demand, advisory). **It's the live test of whether "suggest the next step" is signal or noise.** ⚠️ **Note (2026-06-23): it got iterated A LOT (context-from-transcript, hybrid generation, scroll-back detail view) over 0.7.25→0.7.30 — but those were Zach's bug-reports/feature-requests from using it on his phone, NOT autonomous investment.** Usage signal is therefore AMBIGUOUS: he's clearly looking at the cards (he reports issues), but whether he flags a project for auto-suggest + acts on a generated next-step is still unconfirmed. **Before building MORE (e.g. the offered full-transcript-upload for unlimited scroll-back, or Web Push): get the explicit signal — does he act on a suggestion?** If the cards are just a passive "what did it do" log (which the scroll-back detail view now serves well), that's a different, simpler product than the agentic "next step" loop. Don't let request-driven polish masquerade as validation.
- **Aim by verifier-cost × revenue/time-proximity**, and follow "where is Zach's judgment the bottleneck." Don't aim from recent-toil mining (recency-biased).
- **Recurring MONITORING is a script; the agent is for DISCOVERY/ADAPTATION + escalation — never agent-wash deterministic recurring work.** Once an agent has crystallized the queries (reconstructed a funnel, found the schema gotchas, settled the segmentation), *re-pulling those known numbers on a cadence is a cron job, not an agent* — an LLM there is pure cost + unreliability. Architecture: **deterministic rails (a static script does the recurring pulse + the experiment verifier + diff→living-issue) that ESCALATES to an agent only when it hits something it can't explain** (anomaly / threshold cross / schema break → "why?"). The agentic budget goes to discovery + the genuinely-novel question, not to monitoring. (Zach caught this when "growth-watch standing loop" was really a report script — the discipline applied to ourselves.) Same shape as the durability guard: mechanical check = cheap/deterministic, the context/judgment = where intelligence belongs.
- **AUTONOMOUS loops stick; OPT-IN command-shortcuts don't — measured at 1 week (2026-06-23).** The toil-scan re-check showed: ADS (autonomous: fires without Zach) held up + the infra-investigation toil it targets went quiet; but the productivity *commands* built the same week (`/audit-pr`, `/analyze-service`, `/ux-audit`, `/handoff`) are barely used — the audit ritual is STILL hand-typed (~51×/7d as `typed: audit/review PR`, not `cmd:/audit-pr`). Only `/find-session` stuck (0→3). Even when the audit skill ran, it was because Zach typed the natural-language sentence and the system routed it — he never reaches for the slash-command. **Lesson: "remember to type the command" is itself an input that fails (the reframe holds). The leverage layer is AUTO-TRIGGER or transparent natural-language routing, NOT a slash command the human must opt into.** Don't invest more in opt-in shortcuts; invest in autonomous triggering. (Confounds on the toil-scan deltas: last-week's window included the heavy devrc tooling session that inflated audit/verify counts; main-host-only; this-session contamination — so the *drops* are directional, not precise. The adoption *pattern* — autonomous-yes, opt-in-no — is the durable signal.) **↻ AMENDED 2026-06-23 (operator): the yardstick is "used by HUMAN *or* AGENT," not "does Zach type it." Opt-in fails for HUMAN ritual only; an AGENT-invoked deterministic rail (`ship.sh`; and `/audit-pr` + `/handoff`, which agents DO reach for) is the FAVORED pattern, not the rejected one.** Do NOT re-reject a good agent-rail by citing "opt-in doesn't stick" — that bit us this very session (the CTL run first rejected `/ship` on the stale reading; operator corrected). Small agent-rail consistency/token wins stack ALONGSIDE the 10x lever, they don't compete for it.
- **A code+config PR that doesn't bump the DEPLOYED ARTIFACT is inert on merge — "the code landed" ≠ "the change ships."** (2026-06-17, caught by the adversarial audit on PR #162.) The first cut added `gates.py`/`main.py` code + a new env var but left `deployment.yaml` pinned at the SAME image digest → Flux would have rolled a new env onto the old binary that ignores it. I'd already told the operator "shipped, just merge it" — wrong. The audit (`/audit-pr`) caught it. Durable rule: before claiming a GitOps change is ready, verify the deploy artifact carries the code (image tag **+ digest** bumped, matching the repo's per-commit convention), not just that source changed. Always run the adversarial audit on an agent-built PR before trusting/merging — it caught a 🔴 inert-on-merge + a 🟡 guard-bypass the implementer missed.
- **The capability is often ALREADY BUILT but deliberately TARGETED AWAY from the operator's actual toil — closing the loop = inverting the targeting, not building.** (2026-06-17, the reduce-Zach-input program.) Zach's #1 forced input is reactive alert investigation (`/investigate-alert` 32× + network/orch). Traced it: the full autonomous loop (Alertmanager→ADS auto-diagnose→runs the *same* `investigate-alert` skill→opens a remediation PR vs talos-infra→Zach-merge=gated approve→auto-verify-cleared) was **already LIVE** — but two gates (deviation-gate suppress + `should_auto_investigate`'s `has_context` skip) encoded the assumption *"the expert handles the KNOWN alerts faster, so only auto-handle NOVEL ones."* Zach's new metric ("every input I give is a system failure; the known recurring alerts ARE the toil") **inverts that assumption** → the close was a few-line allow-list that lets recurring-actionable alertnames through the gates, NOT new infrastructure. Generalizable: when an operator still does X manually despite automation existing, first check whether the automation was *tuned to exclude* exactly X (cost/trust-conservative defaults) before assuming a capability gap.
- **A "newly feasible" unlock must have its SIGNAL verified to exist before you build a loop on it (surface→verify applies to the *premise*, not just findings).** The acquisition-channel run (2026-06-17) was aimed because `landingPage` "became readable + carries UTM" — but the data didn't actually carry the external-referrer signal (landingPage = on-site path; UTM on 4.7% of regs, ~95% ChatGPT; the real referrer table dead 8mo). The run's highest value was the **negative result**: it stopped an over-investment in channel-optimization off data that can't support it, and named the real fix (revive plausible / capture UTM+referrer at signup). A bounded read-only run is the cheap way to falsify a premise BEFORE building — "verify the unlock is real" is itself a loop-worthy gate. Productive duds that prevent wasted loops are wins, not failures.
- **The meta-run (F) MUST be recency-INVERTED, or it just exploits the ledger.** A naive F (allocate over recent outcomes) stays in the orbit of whatever we last did (App Blocks / infra) and never surfaces net-new territory — Zach flagged this directly. Design F with two arms: **explore** (grounded in the NORTH-STAR + the *unexplored* system/product surface, NOT the ledger — maps high-value areas with ZERO prior runs, proposes net-new) + **exploit** (ledger-driven, deepen what we started); tag each candidate explore/exploit and **floor the explore share**. Antidote to recency = go UP to the goal (the value-elicitation interview), not mine recent activity — the same move that broke us out of the infra-loop trap originally. A "coverage map" (high-value surface that exists vs. the tiny slice we've run on) is the explore arm's grounding. **The explore arm DISCOVERS the surface by INTERVIEWING Zach (open, recency-inverted questions), NOT by mining artifacts** (operator decision 2026-06-08) — because artifacts only record what's *been done* (recency-biased by definition); the highest-value *unexplored* surface lives in his head. Interviewing is recency-immune (grounded in judgment/goals) and is the automation of the move that originally broke the infra-loop trap. Corollary to the operating model: **the interview run is the one run type that runs in the MAIN context, not a subagent** — you can't delegate "ask the human."

## The reconciler model (CANON — explored + pressure-tested 2026-06-09)
Map of the loops initiative onto the **Kubernetes control-plane** (declared *desired* state → observe *actual* → diff → apply known-action → repeat; level-triggered, idempotent, self-healing, declarative). It HOLDS for the back half, BREAKS productively for the front half, and the break line is the north-star (judgment vs execution).

⚠ **Mechanism note (2026-08-01) — read before acting on this section.** The model below was
written while clawgate still had a **decision-labeling** substrate. That substrate was **REMOVED**
(0.7.18–0.7.24; migration 0011 drops `decision_labels`) — see Validated lessons above. **The MODEL
is unchanged and still CANON.** But wherever it says "decision-labeling," read it as *"the capture
mechanism — which no longer exists and must be rebuilt on checkpoint decisions over
runbook-dispatched work."* Concretely: the **autonomy dial has no live acceptance-rate feed today**,
so "≥90% acceptance retires the action gate" is a design rule with nothing currently feeding it.
Do not go hunting for the metric.


**Two regimes (the central partition):**
- **Spec-authoring (front half) — NOT reconcilable, by construction.** Deciding *what* desired state should be; the spec is the unknown, finding it IS the work. K8s reconciles toward specs, it never invents them. = the explore-F **interview**, the genuinely-novel question, the product *intervention*. This is irreducibly Zach's judgment (the bottleneck). Don't try to make it a reconciler — *graduate* its output into a spec.
- **Reconciling (back half) — IS a loop.** Driving observed → a *declared* desired state: predicate + cheap observe + idempotent known-actions + residual-to-agent + write-back. = durability guard, node-saturation, funnel-*visibility*, "every bet ships with its verifier."
- **Flow between regimes IS the productization pipeline:** interview authors a spec → graduate to a deterministic reconciler → residual escalates back to agent → agent's resolutions write back to the spec (shrinking the residual). The control plane is the steady state; the interview is *how a new controller is born*. You never replace the interview — you do it *once per new spec*, then reconcile.

**The gate, split (the single most useful artifact).** The old gate ("bounded + cheap auto verifier") is really:
> **Reconcilable iff (a) desired state is a cheap machine-checkable predicate over observable state, AND (b) the actuator reliably moves observed → desired.**
> Fail (a) → **manual tool** (perf-deep-dive: "good analysis" has no predicate, nothing to diff). Fail (b) → **experiment, not reconciler** (funnel *intervention*: action→effect link unproven; users fight back; can only A/B + measure). This distinguishes two failure modes the single gate lumped together. (a) explains *why* some verifiers are cheap — the desired state is a predicate, not a vibe.

**What the lens unifies / retro-fits:**
- **"Deterministic rail escalates to agent on anomaly" = a level-triggered reconciler.** Rail = observe→diff→apply-known-action (cheap, on cadence). Agent owns ONLY the residual (the non-declarable part: novel drift, "why," "what should desired be"). Agent's resolution **writes back as a new known-action** (= decision-labeling/compound) → residual shrinks → agent budget freed. The reconciler *learns*; the agent is how it learns the non-declarative parts.
- **`surface → VERIFY → route` = observe-before-apply (level-triggered).** Run-3 false positive (routed a B-suspicion before C-verifying) = applying on a stale/assumed state. The C-run (verify) IS the observe step the B-run skipped. Routing must be **idempotent** (don't route what's already enforced — A6 ledger already did it).
- **Graduated autonomy = a controller earning the right to act unattended.** Two distinct admission gates we'd conflated: **spec-admission** (Zach declares/edits desired — rare, high-judgment) vs **action-admission** (a reconciler wants to mutate prod — frequent, low-judgment-each). Decision-labeling acceptance-rate ≥90% retires the **action** gate, never the **spec** gate.
- **Durability guard already fits cleanly.** Its exceptions list (nats=gap; redis=cache; meilisearch=deliberate; sweeps=too-new) **IS a declared desired-state spec**; the operator's one-bit corrections are *edits to the spec*. It's the readiest chain to run the pipeline on.

**From Zach's perspective (the UX — how a reconciler fleet feels day-to-day):** you stop *running* loops and instead *declare* specs + *adjudicate exceptions*. (1) **Spec-authoring session** (rare, ~15min, main context): you dictate a desired-state predicate + exceptions — not code (e.g. the durability spec). Once per controller. (2) **Steady state: you do nothing** — silence = healthy; level-triggered, so new services/missed runs self-catch; no report you're obligated to read (workslop avoided by construction). (3) **Checkpoint → phone**: only when a reconciler wants to mutate prod or hits known drift — one-bit Approve/Deny/"record-exception"; every tap captured by decision-labeling. (4) **Residual escalation**: novel drift → agent returns a *proposal* (drift + why + proposed known-action), not a report to read; your correction writes back. (5) **Autonomy dial**: after ≥90% acceptance on an action class, system offers to stop asking (retires the *action* gate, never the *spec* gate). (6) **Re-authoring**: periodic "is this spec still right?" mini-interview (the spec-drift fix). Experiments (funnel-fixing) are the exception — UX is *propose + pre-wired-verifier + "ship it, I'll tell you if it moved"*, not converge.

**DECISION (2026-06-09, operator): fleet default = GATE EVERY PROD MUTATION.** Read-only reconcilers run silent; ANY action that changes prod requires a phone checkpoint until that action class earns autonomy via acceptance-rate ≥90%. Blast-radius does NOT auto-grant (rejected the "auto-act on low-blast" option); trust-history does. This is already what the clawgate harness implements (checkpoints + decision-labeling + graduated autonomy) — no new mechanism needed to honor it.

**Where it BREAKS (name the limits):**
- **Unreliable actuator** → product/revenue loops are **experiments, not reconcilers** (gate-b). The funnel-*visibility* sub-loop reconciles; the funnel-*fixing* sub-loop is an A/B. Same chain, two different objects.
- **Can't reconcile what you can't observe** — the funnel's channel blind spots = "the controller has no informer for this resource"; "instrument-to-see" = add the status field before you can write a controller on it.
- **Specs drift on their own / adversarially** — pods don't change what they should be; markets do. Reconcilers assume a fixed target during convergence; growth moves it → needs a periodic "is the spec still right?" which routes back to the interview. The control plane never questions its own specs; our system must.
- **Level-triggered upgrade for one-shot runs:** a one-shot run is edge-triggered (`kubectl apply` once, walk away). Make guards re-derive from current observed state every cadence (robust to missed runs, restarts, new services appearing) rather than firing on an event.

## Loop ledger (condensed — evidence + caveats in `ARCHIVE.md`)

| Loop | Work-unit | Automatic verifier | State |
|------|-----------|--------------------|-------|
| **clawgate agent loop** | one Task | agent opens a real PR → `ready_for_review` | **✅ SOAKED + VALIDATED 2026-07-05** (~7 real tasks, #58–#64). Mechanism proven; ADOPTION is the open question. |
| **repo-cos** (CEO model) | one repo proposal | proposal is `file:line`-evidenced; approve → clawgate Task → PR | **✅ SHIPPED + verified live.** Self-hosted mail send + Postgres reply read; 3 deterministic reply intents. **Discipline: USE, do not extend.** |
| **mail-actions** | one email thread | the ARTIFACT (extracted item vs source; invoice in bucket; reply auto-closes) | **✅ CLOSED end-to-end.** Stage-1 filter drops ~99% pre-LLM. Extractor stays MANUAL by operator choice. |
| **QA/UX-audit loops** (naida/vetr) | one funnel view | **the walk's `guard()` re-throws ⇒ the RUN FAILS** on a broken/missing view (`*.audit.ts`, both repos) + `≥1 screenshot` assert + vetr's 3 `authnet-*` Playwright specs. **NOT `findings.md`** — judging that is a human read, which this skill's own gate disqualifies | **✅ CLOSED day one** — surfaced a LIVE revenue bug (pay-now checkout rendering with a null `client_secret`). **Bug FIXED + shipped** — both checkouts now branch on `useGateway()`, guarded by 3 self-skipping regression specs (verified 2026-08-25; detail in `ux-audit-loops`). |
| **Funnel-visibility (growth)** | one funnel stage | mechanical query result | **✅ CLOSED + handed to product.** #1 leak = activated→paid (97.8% drop). Levers: push first-generation; drive a day-2 return. |
| **Alert-investigation autonomy (ADS)** | one firing alertname | **alert clears** + PR-merge acceptance | **✅ CLOSED — real-traffic verified 2026-06-18.** Closed by INVERTING gate targeting (an allowlist), not by new infrastructure. |
| **CronJob-staleness coverage** | one fleet CronJob | overdue vs `kube_cronjob_next_schedule_time` (PromQL) | **✅ SHIPPED + VERIFIED 2026-06-16** (PR #158). Not fire-tested. |
| **ship (two-host converge)** | one config deploy | both hosts `HEAD==origin/main` + switch rc0 | **✅ SHIPPED + dogfooded.** An agent rail. Caught the laptop silently lagging twice. |
| **Question-generation loop** | one well-posed question + bounded answer | operator up/down; slow ground-truth = did it change a decision | **✅ VALIDATED (3 runs).** Run-type taxonomy CANON: A data/infra · B self/product · C outcome-verify · D diff-triage · E hypothesis · F aim-selection. ⚠ its old "cheap up/down via decision-labeling" verifier **no longer exists**. |
| **Acquisition-channel attribution** | one channel's volume→paid | mechanical query | **✅ RAN — PRODUCTIVE NEGATIVE.** Premise was false; external channel stays blind for ~95% of traffic. **Do NOT build a channel-optimization loop on this data.** |
| **Toil-elimination rail** | one forced-input category | that category's frequency DROPS next window | **🔄 BUILT, escalation pending.** ⚠ Operator: do **NOT** self-run it. Recency-biased by construction — finds toil, never net-new leverage. |
| **auto-audit-on-push** | one pre-push diff | audit routes only 🔴/🟡 | **BUILT 2026-06-23, shadow/OFF.** ⚠ Coverage gap: does NOT fire in `datapacket-talos` (~85% of audits) — that repo pins its own `core.hooksPath`. |
| **Durability-gap guard** | one stateful service | backup exists OR a captured accepted-reason | **⏸ PARKED (operator "not yet").** Real gap set = **`nats` JetStream only**. ⚠ Seed the recorded exceptions (the redis caches, meilisearch=deliberate, sysredis=replicated, new sweeps=too-new) or it runs as noise: a blind audit flagged 4 gaps, operator context reduced it to 1 (75% false-positive). |
| **Node-saturation guard** | one node | memory headroom < threshold (PromQL) | **NEXT — not built.** Born from the node-freeze; reboot stays human-gated. |
| ~~Decision-labeling~~ | one approve/deny | acceptance-rate metric | **⛔ REMOVED** (clawgate 0.7.18–0.7.24; migration 0011 drops the table). Never a closed loop. See Validated lessons. |
| ~~perf-deep-dive~~ | per-service analysis | (human judgment) | **DEMOTED** — fails the gate; manual tool only. |

**Status:** the question-generation loop is validated; the funnel/growth chain shipped end-to-end;
durability monitoring shipped as deterministic sweeps (`dr-sweep` LIVE 2026-06-09, `cert-sweep`
LIVE 2026-06-15, each with its own staleness rule). Parked: the context-capturing refinement, the
`nats` fix, node-saturation.

---

## Harness inventory (what's available — reuse, don't rebuild)
- **clawgate** — the substrate: dispatches kubeclaw agents that clone a repo; **runbooks**
  (parameterized dispatch templates); **checkpoints** (approval → phone); **privilege profiles**;
  a durable **Tasks** adjudication queue with machine create/read/edit/set-status/delete and
  one-tap Dispatch; Prometheus metrics + Faro RUM. Operate via the **`/clawgate` skill** (read it
  for live version + endpoints — it ships concurrently, so do not trust a version pinned here).
  ⛔ **`/ui/decisions` and decision-labeling are GONE** — do not look for them.
- **task-spec drafter** — the deep-context drafter runs as an OpenClaw kubeclaw agent on the
  **homelab** cluster (DeepSeek V4 Pro). Off Zach's Claude subscription, off civitai prod, and it
  keeps real tool-use VERIFY. The devrc/Haiku prototype is retired.
- **repo-cos / mail-actions / initiatives / activity telemetry** — all shipped in `devrc`; each has
  its own skill. Check the skill before building anything adjacent.
- **CIVIT PROD CLUSTER ACCESS** — the DEFAULT kubeconfig/context `admin@civitai-talos` has a
  STALE CA → `x509: signed by unknown authority`.
  The working one is `KUBECONFIG=/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig`
  (context `admin@civit-datapacket-talos`), read-only verification. **Subagents must be told this
  path — they cannot guess it.**
- **Civit fleet** (in `datapacket-talos`): capacity-sweep, reliability-sweep P1–P4,
  dependency-pr-auditor, classifier-tuner, pr-reviewer, alert-diagnose-svc, standup-triage…
  Check before building anything civit-side (the `next-lever` skill + `AGENTIC_LEVERAGE.md` are
  the catalog).
- **Existing artifacts:** `perf-deep-dive` runbook + `homelab-observability-read` profile
  (perf-deep-dive is a **manual tool**, not a loop).

---

## Operating model (validated)
**Question-loop runs execute in a SUBAGENT** (proven by the durability audit: subagent does the grounded dig → returns a structured short list → the coordinating context synthesizes + the operator rates). This keeps the high-level/strategic context focused and is the manual prototype of a future clawgate "question-loop runbook" (same dispatch shape). A run = point a subagent at ONE corpus → it surfaces a *ranked short list* of the most decision-relevant questions (of-data / of-ourselves) + bounded answers → STOP (not open-ended) → operator thumbs-up/down = the cheap verifier + first compound signal. Run types: A data-interrogation · B self/strategy · C outcome-verification · D change/diff triage · E hypothesis-design · F aim-selection (the meta-run — highest leverage, slowest verifier; now ready, a track record of 3 exists). **Taxonomy is now CANON** — A/B/C validated across the durability, App Blocks, and funnel runs.

## The validation bar (a loop is only "done" when)
Runs on **real un-rigged work** · the **verifier fires automatically** · the output is **acted on** · demonstrated across **≥3 real runs** with a measured signal (FP rate / acceptance / win-held).

---

## Pointers
- **`ARCHIVE.md`** (this dir) — shipped narrative, build history, full-evidence ledger rows.
- **`SKILL.md`** (this dir) — the process, the hard gate, the anti-patterns.
- **`/clawgate` skill** — operate the harness (status, deploy, runbooks, telemetry, logs).
