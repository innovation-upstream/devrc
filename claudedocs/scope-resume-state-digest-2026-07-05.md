# Scope: initiative-scoped live-state digest on resume

**Date:** 2026-07-05 · **Status:** scoping (not built) · **Related:** [[agent-shell-env-handles]], shell-env nudge (devrc #48/#50, proven pattern)

## Problem (the sink)

The turn-analysis found the **dominant** avoidable agent cost is not code exploration or shell
plumbing — it's **re-querying external state on session resume**: 100–235 external-state
tool-calls/session spent re-establishing "what's the live state of dp-prod / the alert stack /
this canary right now." Nearly every high-turn session opens *"Continue/Resume X — read the
handoff first,"* then hand-rolls dozens of `kubectl get` / `git` / `gh pr` calls to reconcile the
handoff against reality. Steady-state `kubectl get` ran ~1,047×/30d, overwhelmingly in
datapacket-talos (civitai infra).

This is 5–10× larger than the shell-plumbing sink we just closed.

## What already exists (do NOT rebuild)

| Tool | Scope | Deterministic? | Gap for resume |
|---|---|---|---|
| `standup.sh` (`state`,`deploys`) | **fleet-wide** — every repo/cluster | yes (one script) | too broad; not tied to one initiative |
| `check-cluster.sh` | **whole cluster** health | yes | too broad; not initiative-scoped |
| `verify-deploy`, `triage-dp`, `check-app`, `investigate-*` | targeted drill-downs | yes | manual, per-symptom; not a resume reconciler |
| `/resume` step 3 "re-verify against live state" | the actual resume path | **NO — prose** | tells the agent to hand-roll `git status`/`kubectl get pod`/PR status → the 100–235 turns |

**The precise gap:** there is no *initiative-scoped* collector, and the one path that needs it
(`/resume`) re-derives state by hand because step 3 is prose, not a script. The infra to collect
state deterministically is already proven (standup.sh/check-cluster.sh); it just isn't wired into
resume, and isn't scoped down to "just the slice this handoff is about."

## Proposed design

Mirror the shell-env win: **replace prose with a deterministic collector + wire it into the path**,
then verify over real runs.

### 1. Structured `state-watch` header in handoff docs (deterministic > prose-parsing)
Extend `/handoff` to emit a fenced, machine-readable block naming the initiative's live targets:

```yaml
# state-watch
repo: civit/datapacket-talos
cluster: dp-1           # -> prod-kubeconfig (reuse standup.sh CL map)
branch: zach/api-pool-hol-blocking
pr: 219
deployments: [civitai-dp-prod-api, civitai-dp-prod-api-heavy]
namespace: civitai-dp-prod
canary: civitai-dp-prod-api
alerts_ns: civitai-dp-prod
```

Resume reads this deterministically instead of parsing prose. Existing handoffs lack it → the
collector **degrades gracefully** to git-only + heuristic entity extraction (like initiative-scan
degrades when telemetry is off).

### 2. `resume-state.sh` — the initiative-scoped collector (one script, one turn)
Modeled on `standup.sh` (bash, reduce-at-source, only the digest reaches stdout). Given the
state-watch header it collects **only that slice**:
- **git/PR**: branch ahead/behind, dirty count, last-commit age; `gh pr view <pr>` state (merged?
  CI red? conflicting?) — answers "did the in-flight item land already?"
- **workload**: `kubectl rollout status` / Flagger canary phase for the named deployments (scoped,
  NOT whole-cluster) — new ReplicaSet crashlooping vs old still serving.
- **alerts**: firing alerts filtered to `namespace`/`cluster` (reuse standup's per-cluster split).
- **drift**: diff collected reality against the handoff's own claims → emit `DRIFT:` lines.

Lives in devrc (global, beside `standup.sh`; reuses its repo→kubeconfig map) because `/resume` is a
global command. **v1 targets datapacket** (the ~90% case) and degrades elsewhere.

### 3. Rewire `/resume` step 3
Replace the prose "check it live" bullet with: `bash ~/.claude/skills/resume/resume-state.sh
<topic>` and interpret the digest. Deterministic, one turn, correctly scoped.

### Key decisions (call out)
- **On-demand, NOT cached.** Resume must re-verify against *fresh* state; a cached snapshot risks
  acting on stale reality (RULES: memory-is-hypothesis). One script call per resume, not a daemon.
- **Deterministic header, not prose-parsing.** The `/handoff`↔`/resume` pair carries structure.
- **Scoped, not fleet.** standup already does fleet; this is the one-initiative reconciler.

## The verifier (already built — this is why it passes the gate)
Re-run the exact per-call-timestamp turn-analysis used for shell-env
(`scratchpad/perday.py` pattern): measure **state-query turns per resume-session** (kubectl-get /
git / gh calls in the first N turns of "Continue/Resume …" sessions) before vs after. Cheap,
automatic, over real runs. Success = a measurable drop in hand-rolled state queries on resume +
`resume-state.sh` adoption (like the 0→50% handle adoption we saw).

## Phasing (bounded)
- **P1** — `resume-state.sh` datapacket-only (git/PR + rollout/canary + scoped alerts + drift),
  heuristic entity extraction (no header dependency yet). Wire into `/resume`. Ship, dogfood.
- **P2** — add the `state-watch` header to `/handoff`; collector prefers it, falls back to P1 heuristics.
- **P3** — generalize the repo→cluster map beyond datapacket (homelab, others) if P1 verifies.
- Measure after P1 and again after P2.

## Risks / honest caveats
- **Depends on handoff quality.** No handoff / vague handoff → collector degrades to git-only; still
  better than nothing but not the full win. Existing handoffs won't have the header until P2.
- **Entity extraction is heuristic** (P1) — a deployment renamed since the handoff may be missed;
  the header (P2) fixes this deterministically.
- **The collector itself costs kubectl calls** — but as ONE script call returning a digest (~20–40s,
  standup precedent), not 100 hand-rolled turns. Net win only if `/resume` actually calls it (the
  shell-env data showed adoption is the real risk; the deterministic wire-in + a nudge mitigate it).
- **Cross-cluster reach from laptop** (nebula) — reuse standup.sh's host-aware kubeconfig handling.

## Effort / recommendation
P1 is bounded and self-contained (one bash script + a one-line resume.md rewire), directly modeled
on `standup.sh`/`check-cluster.sh`. Verifier is already built. Recommend building **P1 datapacket-only**
first, dogfood on the next few dp-prod resumes, measure, then decide P2/P3.

**Decision point for Zach:** build P1 now (datapacket-scoped `resume-state.sh` + rewire `/resume`),
or start with the `state-watch` header in `/handoff` (P2-first) so new handoffs are ready before
the collector exists?
