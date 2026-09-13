# Handoff — Initiatives Phase 2a SHIPPED: next-step recommendation + dispatch, 2026-07-26

**Status: shipped, deployed to both hosts, verified end-to-end.** This is the "shipped" successor to
`handoff-initiatives-nextstep-dispatch-2026-07-26.md` (the plan). Operate via the **`initiatives`** +
**`clawgate`** skills.

## What shipped (origin/main `835fe0c`, 3 commits)
1. **Tier 1 — grounded next-step recommendation (READ-ONLY).** New pure `scripts/initiatives/nextstep.py`
   `recommend_next_step(view) -> {text, basis} | None` under a strict anti-confabulation contract: the text
   is always a close paraphrase/quote of a REAL view field, never invented; None when nothing supports one.
   Fixed priority: `next_step` (basis=`handoff`, the parsed documented step) → `open_prs` (`open-pr`) →
   `open_investigations` (`investigation`) → `face_message` (`focus`, your last prompt) → `status`
   (`status`) → momentum==stalled (`stalled`) → None. Only `handoff` is a step you wrote down; the rest are
   labelled INFERRED. Attached in `viewer.build_model` (`recommended_next_step`), rides the view into
   `/api/initiatives.json`. **Card** renders a distinct `next (suggested) ›` line + basis hint ONLY when
   `!v.next_step` (documented cards unchanged) — targets the **Emerging** gap. **Chat**: new
   `recommend_next_step` tool wired through `assistant.py` (INTENTS/run_tool/build_facts/sources_of/
   render_plain + 3 classify patterns), `skills/query.py` catalog, and `skills/initiatives.SKILL.md` — so
   "what should I do next on X" works via the OpenClaw agent AND the regex fallback.
2. **Tier 2 — one-tap dispatch (STRUCTURALLY SAFE, human-tapped).** New `scripts/initiatives/dispatch.py`
   mirroring `repo-cos/clawgate.py` (best-effort, stdlib urllib, NEVER raises): `load_creds` /
   `build_task_title` / `build_task_body` / `resolve_repo_fullname` / `post_task` / `dispatch_initiative`.
   Viewer endpoint **`POST /api/dispatch`** `{repo, slug}` → 200 `{ok,task_id}` / 400 / 404 / 502 (never an
   uncaught 500). Per-card **Dispatch** button (renders only when a recommendation exists). **Trust model:**
   the VIEWER (workbench, LAN-bound, `~/.claude/clawgate.env`) holds the clawgate token and POSTs — the
   in-cluster read-only devpod gets NOTHING new (audit-verified: query.py/assistant.py have zero token/
   dispatch refs). Two human gates: tap in viewer → creates a clawgate Task card; tap Dispatch in clawgate →
   runs. clawgate `/api/tasks` contract (from Go source): `{directory(=display label), body(req), model?,
   repo?, branch?}`; OMIT model → clawgate default deepseek. Task-drafter-style card body.
3. **Audit fix** (`835fe0c`): the lazy `_dispatch()` load was OUTSIDE the error-wrapping try → an import
   failure would be a caught-500 not a 502. Moved inside the try + 2 regression tests for the live
   lazy-load path.

**Tier 3 (write — updating next_step / handoff docs) stays DEFERRED** behind the structural server-side
write-gate per `claudedocs/initiatives-agent-proposal-eval-2026-07-24.md`.

## Verification (memory-is-hypothesis — done, not assumed)
- `444` hermetic tests pass. `/audit-pr`: no 🔴; anti-confabulation, XSS/textContent, token isolation,
  never-raises dispatch, clawgate payload contract, no regressions all positively verified.
- **Real end-to-end:** dispatched `naida-ai · HANDOFF` via the exact production chain → clawgate Task **#68**
  created (`repo=ZacxDev/naida-ai`, `model=''`→deepseek), confirmed by an independent `GET /api/tasks`.
- **Deployed** to both hosts via `scripts/ship.sh` (origin/main `835fe0c`); live viewer serves
  `/api/dispatch` (bad body→400, unknown slug→404 with the new error message).

## Loose ends / notes
- **Emerging-lane slugs are ugly** (sorted-token strings) — the dispatch `directory` label uses
  `repo_name · slug`, so an ugly slug → an ugly-but-harmless label. Tunable with the discovery floor.
- **A `handoff`-basis recommendation can be a mid-sentence heading fragment** (the scan parses the handoff's
  "next steps" heading). Faithful/grounded, occasionally truncated — acceptable; improving the scan's
  next-step parse is a separate nicety.
- **Stale-snapshot**: the dispatched body is rebuilt from the TTL-cached view at POST time (freshest wins;
  the clawgate human-gate is the backstop). Acknowledged, acceptable.
- **Loose ends inherited (not addressed this session):** clawgate embed still at kubeclaw 0.7.0 (0.7.1
  re-sync pending); workbench/client devpods unhardened; the dirty local `homelab-talos` checkout.
- **Concurrent-session hazard observed:** this devrc working tree was under heavy concurrent git activity
  during the session (my build agent's 2 commits initially landed on `main` not the feature branch, the
  shared HEAD moved under me, transient ref-lock `rev-parse` failures). Resolved safely — origin/main is
  clean at `835fe0c`, the feature branch + main coincide, and ship.sh's stash→pop restored the concurrent
  session's uncommitted `nix/home.nix`. If dispatching parallel agents against a busy repo, use worktree
  isolation.
