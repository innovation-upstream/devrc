# Handoff — Initiatives Agent Phase 1 SHIPPED (model-driven OpenClaw agent + skills), 2026-07-25

**Supersedes** `handoff-initiatives-agent-phase1-2026-07-24.md` (the kickoff). Phase 1 is **built,
verified live, and shipped**: the read-only initiatives assistant is now a **model-driven OpenClaw
devpod agent** — the model does intent-understanding + tool-selection, deterministic skills query the
store, sources are computed from tool output. The brittle regex intent-classifier is **retired from the
routing role** (kept only as a graceful fallback). Operate it via the **`initiatives` skill** (updated).

## What shipped (all verified against live state)

1. **Model decision (the gate).** Validated the local `vllm-recap` **Qwen2.5-7B-Instruct-AWQ** as a
   single-shot JSON **tool-selection planner**: 13/15 full-pass, **compound 4/4**, stable over 3 runs
   (harness: `scratchpad/toolsel_eval.py`). It correctly returns BOTH tools for "whats stalled and
   waiting". BUT the endpoint has **no `--enable-auto-tool-choice`**, and a 7B can't drive a full
   OpenClaw agentic loop → the devpod's backing model is **DeepSeek V4 Pro via OpenRouter** (Zach's
   dispatch default, [[task-dispatch-default-deepseek]]). The 7B finding stays relevant if we ever
   want a cheaper local planner step.

2. **Skills (devrc `scripts/initiatives/skills/`).** `query.py <tool> [--target]` → grounded JSON,
   REUSING `assistant.py`'s pure `run_tool`/`build_facts`/`sources_of` verbatim (tools KEPT, regex
   routing RETIRED). `initiatives.SKILL.md` = the agent's tool-selection brain (compound-decomposition,
   anti-confabulation, read-only). Store reached via `_db.py` **direct in-cluster mode (#156)** — no
   port-forward in-pod. Tests: `test_query.py` (17) + `test_agent_client.py` (32) → **suite 333 pass**.

3. **Least-privilege DB role.** `initiatives_agent` on the homelab **mailbox** Postgres — **SELECT-only
   on `initiatives.*`**; INSERT + the `mail` schema are denied (verified). DSN in the sops secret as
   `MAILBOX_PG_DSN`. NOTE: SELECT-only can't run `assistant.py`'s log self-heal (`CREATE TABLE IF NOT
   EXISTS` needs schema CREATE even when the table exists) → the **audit-log write stays viewer-side**.

4. **Devpod (homelab `devpod-initiatives`).** kubeclaw HelmRelease at
   `homelab-talos clusters/homelab/apps/agent-pods/initiatives/` (modeled on `task-drafter`). Clones
   PUBLIC devrc over HTTPS; `extraInitCommands` apt-installs `python3-psycopg2/requests` and **PINS the
   agent identity** (removes OpenClaw's onboarding `BOOTSTRAP.md`; without this, first-person "what am I
   working on" made the agent talk about ITSELF). **Namespace read-only RBAC only** (no cluster RBAC —
   tighter than task-drafter); no trigger cron. Applied SURGICALLY (agent-pods flux Kustomization is
   `suspend=true`); live secret via kubectl from local plaintext (workbench lacks the homelab age
   private key), git `.enc.yaml` is the GitOps record.

5. **Viewer `/api/ask` → agent.** `agent_client.py` proxies to the gateway (kubectl port-forward, token
   `sha256("gw-"+HOOKS_TOKEN)` read from the in-cluster secret), **deterministic whole-token slug
   sources** (fixed a substring false-positive found in review), reuse of the `assistant_log` write
   (`intent=agent`). `viewer.build_asker` = agent-first with **graceful fallback to the regex
   assistant** if the devpod is down. Env on the viewer unit (`nix/home.nix`): `INITIATIVES_AGENT_ENABLED`
   + `AGENT_*` (+ `RECAP_*` kept as the fallback model). Shipped via `home-manager switch`.

6. **Verification (the audit-log loop).** `POST /api/ask "whats stalled and waiting on me"` →
   `intent=agent`, runs BOTH `stalled`+`blocked_on_me`, grounded ("nothing stalled" + spend-analytics
   waiting), audit row id 7 nsrc=6. Compare the OLD regex row: `intent=stalled` (collapsed the compound),
   **0 sources** (missed the waiting item). **The exact bug is fixed.** First-person "what am I working
   on" → 12 grounded active initiatives (post identity-pin).

## Live state
- Agent: HelmRelease `initiatives-agent` (flux-system) → pod `initiatives-devpod-*` in
  `devpod-initiatives`, chart kubeclaw@0.5.2, image `openclaw-image:2026.6.1`, model
  `openrouter/deepseek/deepseek-v4-pro`, gateway `svc/initiatives-devpod:18789`. Tracks devrc `main`.
- Viewer: `http://192.168.50.250:8899` (workbench), agent-enabled, regex fallback.
- Merged: devrc `main` (query.py/SKILL.md/agent_client/viewer/nix); homelab-talos `trunk` (`71c9dbb2`).

## ⚠ Loose ends / caveats
- **Concurrent homelab-talos work** (another session: remix + vllm-joycaption + many `claudedocs/`) was
  UNCOMMITTED and got briefly swept into my first commit; unwound cleanly (my commit pushed via a
  throwaway worktree, their work untouched in the working tree + backed up in `git stash@{0}`). That
  session still needs to commit/reconcile its work with the remix advance on trunk. **Do not drop
  `stash@{0}` until confirmed.**
- The devpod pod's local git branch may read as the old feature name (PVC-persisted clone); it PULLS
  `origin main` via autoPull, so content tracks main. A clean `git checkout -B main origin/main` in-pod
  was run once; a PVC wipe re-clones `main` fresh.
- Each `/api/ask` spawns a kubectl port-forward + a full agentic loop (~13-23s). Fine for a sidebar; if
  it feels slow, a persistent gateway route (nebula/NodePort via `AGENT_BASE_URL`) skips the port-forward.

## Phase 2 (deferred — needs a STRUCTURAL gate, per the eval)
Write/dispatch tools behind a **server-side write-gate** (NOT the voluntary `agent_checkpoint`) +
the least-priv role widened only as needed. Dispatch (clawgate `POST /api/tasks`, `directory` field —
NOT `title`) is structurally safe (card only). Reuse the SAME skills; the read-only devpod is the
foundation, not a throwaway. See `initiatives-agent-proposal-eval-2026-07-24.md` §5.
