# Handoff: clawgate agent-chat polish + structured transcript + e2e green — 2026-07-05

## Goal
Continue the clawgate "USE + SOAK" phase: the Task→Dispatch→agent→PR loop is proven; this session was soak-driven agent-chat UX polish + a structured transcript + greening the e2e suite. Keep USING the loop, not building new loop features.

## State now
- **Repo:** `~/workspace/homelab-talos`, GitOps from `trunk` (commit = deploy). Trunk head `3a4b489a`. All work pushed; nothing in flight.
- **Live:** `harbor.homelab.lan/library/clawgate:0.7.66` on the workbench cluster, healthy. Verified live per feature (below).
- **Authoritative point-in-time state:** `homelab-talos/containers/clawgate/HANDOFF.md` (current through 0.7.66 — read it first). Operate via the **`/clawgate` skill** (updated this session).
- **Shipped this session (0.7.59→0.7.66), each deployed + live-verified:**
  - 0.7.59 mobile dispatch-confirm stack + one-row chat header + gated feedback Send
  - 0.7.60 task push notifications flatten markdown (`notificationText()`) — a Web Push body is plain text everywhere, not an Android limit
  - 0.7.61 **idle-task reaper** — daily sweep auto-dismisses >7d-idle tasks + tears down their pods (env `CLAWGATE_TASK_TTL`, `off`/`0` disables; centralized in `dismissTask`)
  - 0.7.62 markdown chat title + status icon removed + sticky autoscroll
  - 0.7.63 **scroll ROOT fix** (`h-dvh` app shell → `#chat-log` is the real scroller) + live tool/text interleave + task-detail modal
  - 0.7.64 **STRUCTURED transcript** — turn persists as ordered parts (migration **0015**, `PartCollector`), full response with interleaved tool chips on reload
  - 0.7.65 **dismissing a task regroups sections LIVE** (broadcast `EventTaskChanged`)
  - 0.7.66 full-height input (in-flow `flex-none`, no dead gap) + scroll-to-working (`forceScroll`) + drop the openclaw `NO_REPL` "no reply" sentinel (`isSentinelReply`)
  - **e2e suite GREENED (73 pass / 2 skip):** `login()`→`waitAppSettled` (fixes FAB detach flake), retries 1/2 + 45s timeout, `NoopProvisioner` (env `CLAWGATE_FAKE_PROVISIONER=1`, inert in prod) enables `agent-chat.spec.ts`. Committed `2f12ad03`.
- **Soak:** ~7 real test tasks dispatched (#58–#64), each produced a genuine PR against `innovation-upstream/devrc` — loop works end-to-end. **Task #64 (`.shellcheckrc`) is OPEN, ready to dispatch** for the next feedback pass.
- **Skills/docs updated:** `~/.claude/skills/clawgate/SKILL.md` (version log→0.7.66, migrations→0015, e2e→73/2 + greening + gotchas, deploy VER→0.7.67); `~/.claude/skills/close-the-loop/STATE.md` (loop SOAKED+VALIDATED note). clawgate `HANDOFF.md` current on trunk.

## Open investigations — live diagnosis state
_No mid-diagnosis bugs. Two known, non-blocking items:_

### e2e full-suite flakes under high box load (not a code regression)
- **Symptom:** `tasks.spec.ts` FAB tests (`#fab-tasks` `toBeVisible`) time out when the shared workbench box is at load ~9.
- **Observed:** with load ~9 the FAB resolves but paints late → Playwright times out; in isolation under normal load it passes; full suite was green twice (73/2). Root aggravator found + fixed at source (`waitAppSettled`). **19 leaked `clawgate-e2e-pg-*` containers** (from `make e2e` runs killed by `timeout` before fixture teardown) were starving the box — cleaned this session.
- **Ruled out:** my chat changes (they don't touch the FAB/tasks path; suite green at 0.7.65 before them).
- **Leading hypothesis:** pure resource starvation on the shared box (a farm-web Playwright run under `appuser` was also live).
- **Next probe (if it recurs):** `docker ps -q --filter name=clawgate-e2e-pg | wc -l` (clean leaks) + `uptime`; run the suite when load is low.

## Next steps (ranked)
1. **Keep soaking** — dispatch **task #64** (`.shellcheckrc`), do a feedback pass on the 0.7.66 chat (input flush / working-bubble scroll / no `NO_REPL`). Resist building new loop features.
2. If more chat feedback lands, batch into one version; always `git -C ~/workspace/homelab-talos fetch origin trunk` + check the LIVE pin before numbering (Zach ships concurrently).
3. (Chore, not urgent) Add a trap/`--forbid-only` so a killed `make e2e` stops leaking `clawgate-e2e-pg-*` containers.

## Gotchas / decisions / dead-ends
- **Deploy = worktree off `origin/trunk`** (never the permanently-dirty main checkout — it has uncommitted `.sops.yaml`/`.serena` and is far behind). Build docker → smoke → push harbor → bump pin → commit explicit paths → rebase → push → flux reconcile → wait for pod → live-verify. Full recipe in the `/clawgate` skill.
- **Playwright vs the agent-detail page:** use `waitUntil:'domcontentloaded'` (NOT `networkidle` — the SSE `/events` conn never idles); for elements that read "not stable"/"outside viewport" under SSE layout churn, use `evaluate(el=>el.click())` / class-toggle assertions.
- **`NO_REPL`** is an openclaw *gateway* sentinel (not clawgate) emitted when a turn ends via tool calls with no closing narration — filtered in `isSentinelReply` (render + `PartCollector`).
- **Structured transcript** persists parts via a `PartCollector` that OBSERVES the stream — NO signature churn to `runToolLoop`; the tool loop still returns only its final text, the collector captures the full ordered turn.

## How to verify
- Live health/pin: `KUBECONFIG=$KC_WORKBENCH kubectl get pod -n clawgate -l app=clawgate -o jsonpath='{.items[0].spec.containers[0].image}'` → `clawgate:0.7.66`.
- Chat fixes (live, on a real task chat e.g. lively-lynx/task 63): input flush to `#chat-log` (gap ≈8px, was 43), `#chat-form` not `fixed`, no `NO_REPL` bubble, structured tool chips present.
- e2e: `make -C ~/workspace/homelab-talos/containers/clawgate e2e` → 73 pass / 2 skip (when box load is normal; clean `clawgate-e2e-pg-*` leaks first).
- Go: `go test -race ./...` + `bats hook/tests/*.bats` green.
