# Handoff: the task-spec drafter + the 10x productivity thread — 2026-06-24

**Repo/threads:** spans `devrc` (harness), `civit/datapacket-talos` (ADS, A-1), `homelab-talos`/`ZacxDev/homelab-infra` (the drafter agent). The living ledger is `~/.claude/skills/close-the-loop/STATE.md` (read its START HERE first — it's current as of this handoff). This doc is the session capture.

---

## Copy-paste kickoff for the next session
> Continue the productivity / task-spec-drafter thread. Read first: `devrc/claudedocs/handoff-task-spec-drafter-2026-06-24.md` and `~/.claude/skills/close-the-loop/STATE.md` (START HERE). The deep-context task-spec drafter is LIVE in SHADOW as a DeepSeek-V4-Pro kubeclaw agent on the homelab cluster, verified producing good cards. The next move is to accumulate a few daily shadow runs, read them, and graduate shadow→live. Don't rebuild — verify and graduate. App Blocks *execution* is handled in other sessions; don't touch it.

---

## What this thread is (the reframe that matters)
Started as "build productivity commands," but the real finding drove a reframe:
- **Opt-in commands don't get adopted** (measured: a week after building `/analyze-service`/`/find-session`/`/ux-audit`/`/audit-pr`/`/handoff`, only `/find-session` saw real use; the audit ritual was still hand-typed ~51×/wk). **"Remember to type the command" is itself an input that fails.**
- So the leverage layer is **autonomous (fires on its own)**, not opt-in. Zach's directive: *"treat all my input as a failure of the system to figure out the right next step."*
- Then: execution is **already** autonomous (clawgate auto-approve handles most tasks). So the bottleneck moved upstream to **NEW-TASK SPECIFICATION**. **10x = autonomously verifying + triaging inbound into decision-ready cards Zach adjudicates, instead of authoring specs himself.** That is the task-spec drafter.

## THE MAIN DELIVERABLE — the deep-context task-spec drafter (LIVE, SHADOW, VERIFIED)
**Canonical implementation = a kubeclaw OpenClaw agent on the HOMELAB cluster, DeepSeek V4 Pro via OpenRouter.** Runs off Zach's Claude subscription AND off civitai prod, while keeping the deep-context tool-use VERIFY (the value vs a tools-less classifier).
- **Location:** `ZacxDev/homelab-infra` → `clusters/homelab/apps/agent-pods/task-drafter/` (helmrelease, rbac-readonly, trigger-cronjob, trigger-configmap=`trigger.py`, SOPS secret, README). Chart `kubeclaw` 0.5.2, image `ghcr.io/zacxdev/openclaw-image:2026.6.1` (public GHCR, no pull secret), model `openrouter/deepseek/deepseek-v4-pro`.
- **The loop:** daily CronJob `task-drafter-trigger` @ 13:00 America/Winnipeg → fetch ClickUp "To Schedule" view (`6-901111220963-1`) → **delta-scope** (PVC state, only new/changed) → POST each to the agent gateway → agent runs ENRICH→VERIFY (git over mounted `civitai/civitai` read-only + ClickUp; `gh` PR-search NOT yet authed)→CORRELATE→CLASSIFY→DRAFT one decision-ready JSON card → trigger applies the deterministic `safety_gate()` post-processor (security/money/destructive → force NEEDS-DECISION) → routes action-worthy cards (TASK/NEEDS-DECISION/VERIFY/DUPLICATE/safety-flagged; drops already-done/stale/FYI) to clawgate.
- **Where it surfaces:** **SHADOW now → logs only** (`kubectl -n devpod-task-drafter logs job/<trigger>`). When live (`CLAWGATE_MODE=on` + add `CLAWGATE_HOOK_TOKEN` to the secret) → ONE `type:"permission"` digest card per daily run to **clawgate** (`/api/send`, `192.168.50.250:30302`) = a card on Zach's phone listing the action-worthy tickets with their drafted goal/recommendation.
- **VERIFIED 2026-06-24** (read the live shadow cards): DeepSeek V4 Pro produces **Opus-tier** verification — real `file:line` + commit SHAs, git-log checks, ticket correlation (duplicate-of/adjacent-to), honest limits. **Critically it shows the judgment Haiku LACKED** — on the Merch+Blue-Buzz ticket it self-chose NEEDS-DECISION and flagged the payments risk on its own (Haiku confidently mis-drafted these; needed the gate to save it). It's the keeper.
- **Caveats:** ~6 min/ticket (fine for daily delta of a handful; the 25-ticket first-run is ~2.5h); `gh` not authed in-pod (wire `GITHUB_TOKEN` for PR-search); real DeepSeek $/ticket not yet measured (metered OpenRouter — check usage); the safety gate **over-fires** (≈9/10 → NEEDS-DECISION on incidental keywords), so today the win is *faster decisions* (pre-verified glance), not auto-dispatch — tune gate false-positives later to recover auto-dispatch on the safe class.
- **A second prototype exists** in shadow: `devrc/scripts/task-spec-drafter/` (Haiku via `claude -p` on Zach's subscription, local systemd timer). **Retire it** once the homelab/DeepSeek one proves out — it's superseded.

## What else shipped this arc (the infra focus-shield — funds the above)
- **ADS alert auto-investigation** (`civitai/talos-infra` PR #162 + #163, both MERGED + VERIFIED on real traffic). Inverted the deviation-gate so recurring known alerts auto-investigate instead of staying silent; allowlist now `MongoDisk*`, `CivitaiDpProdHighLatency`, `CivitaiDpProdUpstreamTimeout` (the operator dropped the Redis ones via `e0a449558`, retuning them instead). Confirmed firing on real alerts, 0 errors, conservative on Draft-PRs.
- **Rules-layer cut** (Algorithm pass): `~/.claude/RULES.md` 331→90L, `PRINCIPLES.md` 60→8L, BOTH hosts. Reversible via the `.bak-20260616-2049*` files.
- **auto-audit-on-push hook** (`devrc/githooks/`, shadow/flag-off) — auto-runs `/audit-pr` on feature-branch push. Does NOT fire in `datapacket-talos` (it pins its own hooksPath). Opt-in install.
- Productivity commands (`/analyze-service`, `/find-session`, `/ux-audit`) + the standing "subagent+tests+PR" CLAUDE.md rule, both hosts — but see the adoption verdict above (mostly unused; don't invest more here).

## Ranked next steps
**UPDATE 2026-06-24 (graduation session): #1, #2, #3 are DONE.** See STATE.md START HERE for the full record.
1. ✅ **GRADUATED shadow→LIVE** — live cluster patched (`CLAWGATE_HOOK_TOKEN` in secret + `CLAWGATE_MODE=on` on cronjob); send path verified end-to-end (homelab pod → clawgate `/api/send` HTTP 200 + a labeled test card landed). Nothing auto-fires until the daily 13:00 Winnipeg cron. **Card quality re-verified** (5 fresh cards from the live first-run, all with real file:line citations + correct self-chosen NEEDS-DECISION judgment). **Caveat:** live kubectl edit, NOT yet mirrored to homelab-infra trunk — safe because agent-pods Flux is operator-suspended; commit to trunk for durability when convenient.
2. ✅ **`gh` PR-search already works in-pod** (gh 2.93.0, authed as `CivitaiDevOpsAgent`, `gh search prs` returns results) — wiring done; only a prompt nudge left so VERIFY actually calls it. **$/ticket MEASURED: $0.027** (n=9 live tickets; median $0.026) → ~$0.11/day delta. ~5× cheaper than Haiku, ~21× cheaper than Opus.
3. ✅ **Retired** the `devrc/scripts/task-spec-drafter/` Haiku prototype — systemd `.timer` disabled+unlinked (no longer double-triages on the Claude subscription); scripts remain on disk.
4. **Tune the over-firing safety gate** (incidental-keyword false-positives) to recover auto-dispatch on the genuinely-safe class — only worth it once the live soak shows it matters. **Plus:** finding — shadow cards are NOT persisted (only `processed.json`), so clawgate IS the review surface; and the ~25-ticket backlog the first-run baselined will NOT surface (trickle-only going forward) unless you run an on-demand live backlog pass (reset `/state/processed.json` → manual job with MODE=on; ~$0.68, ~2h).

## Open loose ends (not the lever — route/decide when convenient)
- **A-1 spine-controller crashloop (civitai dp-1):** Flux image-automation auto-shipped broken **v2.17.3** (the whole v2.17.x line is broken — Kiota serializer bug in `civitai/civitai-spine-controller`, `WorkerRegistrationManager.cs:589`). Degraded-not-down (old v2.16.15 still serving). Manual rollback gets re-bumped by Flux (semver `>=1.0.0`). **Handoff ready:** fix-forward v2.17.4 (register the JSON serializer) OR constrain the ImagePolicy semver. **Needs the source-repo owner.** Bigger systemic finding: **image-automation has no health gate** — it'll keep auto-shipping broken tags. Candidate loop.
- **Homelab infra findings (surfaced during the deploy):** `openebs-hostpath` provisioner is BROKEN (logs provisioned, never creates the dir → PVCs hang; used `local-path` instead); `.sops.yaml` had 10 rules silently stripped in the working tree pre-session (caught + restored additive-only — worth checking why).
- `agent-pods` Flux Kustomization on homelab is operator-SUSPENDED (~37 agents); the task-drafter was deployed via direct `kubectl apply` matching the committed trunk manifests — Flux adopts it cleanly when you resume agent-pods.
- The `kubeclaw-agents` SKILL.md (`datapacket-talos/.claude/skills/`) is civitai-cluster-scoped; the homelab task-drafter reuses the pattern but is NOT in its "Existing agents" table by design. If folding in homelab notes later, the new gotchas are: deepseek-v4-pro works on image `2026.6.1` (Gotcha #4 cleared), and homelab's `openebs-hostpath` is broken (use local-path).

## Re-entry / verify commands
```bash
# the drafter's shadow cards (homelab):
export KUBECONFIG=/home/zach/workspace/homelab-talos/homelab-kubeconfig
kubectl -n devpod-task-drafter get pods,cronjob,jobs
kubectl -n devpod-task-drafter logs job/<latest-trigger-job>            # the one-line outcomes
kubectl -n devpod-task-drafter logs <agent-pod> -c openclaw-log-tailer  # the full JSON cards
# fire a run on demand:
kubectl -n devpod-task-drafter create job --from=cronjob/task-drafter-trigger td-manual-$(date +%s)

# civitai prod (ADS, A-1):  export KUBECONFIG=/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig
```

## Gotchas
- Civitai prod cluster default context `admin@civitai-talos` has a STALE CA — use `KUBECONFIG=.../datapacket-talos/prod-kubeconfig` (context `admin@civit-datapacket-talos`).
- The drafter's "$/ticket" Haiku figures earlier in STATE.md were the API-LIST-PRICE equivalent (Zach's `claude -p` runs on his Claude SUBSCRIPTION, not a metered key) — the real cost there was subscription quota. The homelab/DeepSeek path is genuinely metered OpenRouter (the whole point).
- "Verify don't assume" bit twice this session (the A-1 diagnosis was inverted; a cross-source incident "correlation" was a false merge) — re-verify diagnoses against live state before acting.
