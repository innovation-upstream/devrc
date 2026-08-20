# clawgate — version history

Read when: you need to know *when* a feature landed, why something was built the way it
is, or whether a behaviour you're seeing is a deliberate old decision. **Not needed for
routine operation** — the core SKILL.md carries everything you need to deploy, drive the
machine API, or debug.

⚠ **This file stops at 0.7.79 and the product runs ahead of it** (live was **0.7.85** on
2026-08-12). An absence here is NOT evidence a feature doesn't exist — for anything newer than
0.7.79, read `containers/clawgate/HANDOFF.md` or `git log` instead.

🔑 **Never derive the next release number from this file** — always from the LIVE
deployment pin (see the core's deploy section).

## Phases
- **0–1** permission approval (replaced the dead Telegram notify-bot).
- **2** Tasks/Repos/Agents tabs on Postgres.
- **3** agent self-service + privilege profiles + the Operator.
- **4** runbooks — parameterized, privilege-aware dispatch templates with approval checkpoints.
- **Production hardening** Wave 1 (resilience/security/correctness) + Wave 2 (CI + observability).
- **Mobile UX pass** — per-project auto-approve, inline task quick-add.
- **Streaming agent chat** (live text/thinking/tool-use) + per-agent model switching.

## 0.2.x
- 0.2.0 — `DELETE /api/response/{id}` broadcasts resolved (fixed cards not disappearing).
- 0.2.1 — client fetches build URLs from `location.origin`, fixing
  `fetch ... URL that includes credentials`.

## 0.3.x
- 0.3.6 — service-worker cache fix: `app.css` is now network-first, cache `clawgate-shell-v2`
  (was cache-first under a never-bumped cache → returning users kept stale CSS).
- 0.3.10 — Phase 2 current.
- 0.3.20–0.3.25 — Phase 3.

## 0.4.x — Phase 4 runbooks + token/clone hardening
- 0.4.0–0.4.2 — runbooks; tokenless clone via credential helper (0.4.1); unspecified branches
  resolve to the repo's GitHub default branch, not hardcoded `main` (0.4.2).

## 0.5.0–0.6.0 — production hardening
- 0.5.0 broke on CSP; **0.5.1 hotfix added `'unsafe-eval'`** (htmx `hx-on` uses `new Function`).

## 0.7.3–0.7.17 — chat / agents-tab UX overhaul
Operator-as-FAB; full-height markdown chat (incl. GFM tables); **multi-session** chat with a
history drawer; a searchable autosave model selector; agent-card rename / ⋯-menu / live-status;
a worker prompt grounded in the agent's real repo + kubeconfig; an **agent-reply notification
FAB** (per-agent unread → session); session-id-in-URL; a consolidated **auto-approve card**
(per-project approved count + remove-on-expiry).

## 0.7.18–0.7.24 (2026-06-11)
- **The decision-labeling loop was REMOVED** — `/ui/decisions`, the `decision_labels` table and
  its metric captured per-turn self-approvals = noise. **Migration 0011 drops the table.**
- **Agent chat FOLDED into the SPA shell** — real sidebar + hamburger, no back arrow; sidebar
  tabs are boosted `<a>`; the standalone `comboboxScript` was deleted — `appScript` is now the
  single combobox implementation.
- **Notifications gained an ACTIVE section** (agents thinking/responding).
- New worker tool **`agent_list_pull_requests`** (lists the agent's repo PRs server-side via the
  stored GitHub token).
- Dispatch-modal fixes (combobox `hx-target` inheritance, modal-close scoping) + a
  privilege-grant-at-create checklist.
- **"Suggested next step"** — a `Stop` hook → per-project-opt-in OpenRouter suggestions in a new
  💡 **Suggestions** tab; **migration 0012**; 14d retention.

## 0.7.25–0.7.30 (2026-06-13→15) — Suggestions matured
- Cards show **"where the session left off"** — the session's last assistant turn, derived
  **server-side from the transcript**, since the real `Stop` hook ships NO usable message field.
  🔑 The transcript JSONL **schema varies across Claude Code versions** → parse structurally,
  never by field order.
- **Hybrid generation** — the LLM EXTRACTS Claude's own proposed next steps from its last
  message rather than re-inventing them (cheaper, grounded).
- **Session detail view** — tap a card's context → `GET /suggestions/{sessionID}`: a scrollable
  page of recent history as readable user/assistant turns, opens at the latest, suggestion
  pinned below.
- Hook tail bumped 16KB → **512KB** for meaningful scroll-back. That is the *stored tail*, not
  the full multi-MB session; true unlimited would need transcript upload to object storage.

## 0.7.31–0.7.35 (2026-06-24)
- **Usage telemetry** — Grafana **Faro RUM** + new Prometheus metrics + the `clawgate-usage`
  dashboard. See `~/.claude/skills/clawgate/reference/telemetry.md`.
- RUM-surfaced **error-toast fix**: `toast` was IIFE-local, now `window.toast`.
- **Per-project auto-approve now PERSISTS** — **migration 0013** + `internal/autoapprove`;
  survives restarts; 1h/8h/24h/indefinite windows.
- Card cleanups: clipped duration menu → in-flow chips; removed unused host/tool badges;
  countdown scales to days/hours.

## 0.7.37–0.7.42 (2026-06-25)
- 🔑 **0.7.37 — clawgate's OWN human auth was REMOVED.** No more `/login?token=` magic-link, no
  session cookie, no `CLAWGATE_AUTH_TOKEN` / `CLAWGATE_SESSION_SECRET`, no Traefik htpasswd
  basic-auth. Public `clawgate.zacx.dev` is fronted by **Authelia 4.39 passwordless passkey**
  (portal `https://login.zacx.dev`, forward-auth at the production nebula gateway; user `zach`,
  passkey enrolled — memory `authelia-passkey-sso`). The LAN (`clawgate.workbench.lan` +
  NodePort `:30302`) is **OPEN, no auth** (trusted LAN). Only the machine hook token
  (`CLAWGATE_HOOK_TOKEN`) remains, gating `POST /api/send`, `POST /api/tasks`, and the
  `/api/response/{id}` poll.
- **0.7.39** — the durable Tasks adjudication queue: the task-spec drafter posts verified specs
  as durable Task cards via `POST /api/tasks`, replacing the evicting `/api/send` digest.
- **0.7.40** one-tap Dispatch per card · **0.7.41** markdown-rendered bodies
  (`internal/ui/markdown.go`: bold/italic/code/fenced-collapsed/blockquote/lists/links) ·
  **0.7.42** cards are collapsed `<details>` disclosures (status + snippet + Dispatch visible).

## 0.7.44–0.7.58 (2026-07-02→03) — the agent loop CLOSES end-to-end
- **0.7.44** machine `GET /api/tasks[/{id}]` reads let a producer poll a Task's status.
- **0.7.45** a Task now carries a **dispatch config** (model / repo / repo_branch /
  grant_profiles; **migration 0014**) so the from-card Dispatch is a pre-filled confirm, not a
  blank form. repo-cos + the drafter fill the config on `POST /api/tasks` (repo-cos resolves the
  repo's GitHub `owner/name` via git remote).
- **0.7.48** dispatched agents CLOSE THE LOOP: `AgentInstructions` tells a repo-backed TASK agent
  to branch → commit → push → open a PR → `agent_comment_task`(PR url) →
  `agent_set_task_status ready_for_review`.
- 🔑🔴 **0.7.49 — agent `git push` only works via a FILE-based credential.** openclaw's exec
  sandbox **STRIPS `$GITHUB_TOKEN`** from the agent's shell (the container env has the 40-char
  token; the agent's own `printenv` shows 0), so the git helper reads **`/root/.gh-token`**,
  written at pod startup by `buildHelmValues` `extraInitCommands`. Memory
  `openclaw-exec-sandbox-strips-env`. Also **`gh` is NOT in the clawdbot image** → PRs open via
  the REST API.
- **Tasks UI**: index **[Requests|Tasks] segment + horizontal swipe**; tasks grouped
  In-progress / Open / collapsed-Done; **live dispatch feedback** (new `agent.changed` SSE →
  card status chip + big Open-chat CTA, no reload); pending-count badge on the Requests segment;
  **optimistic auto-approve**; relative timestamp; **discoverable dismiss** (delete hoisted to
  the always-visible action row). **0.7.56 — dismissing a task tears down its linked agent pod**
  via `Provisioner.Destroy`.
- **Agent chat**: **LIVE kickoff streaming** — the server-side kickoff turn streams
  message → thinking → response via a new **`agent.stream` SSE** + a shared JS renderer (WS +
  SSE), guarded so the `chat.reply` refresh can't clobber it (0.7.50 + 0.7.57); a **completion
  banner** (✅ Agent finished → **Mark complete** navigates to /tasks + a feedback box); smaller
  header / model (name-only) / repo; markdown code-protection (no md inside `` `code` ``) +
  clickable bare URLs.
- **0.7.53 — PWA task-lifecycle push** (created [coalesced 4s debounce] / provisioned / done);
  new `Provisioner.SetStreamHook` / `SetChatReplyHook` / `SetStatusChangeHook` decouple the
  agents pkg from api/push.
- Default dispatch model = **deepseek** (`CLAWGATE_AGENT_MODEL`); the DONE push + close-out fire
  on `ready_for_review`.

## 0.7.59–0.7.66 (2026-07-03→05) — soak-driven chat polish, structured transcript, reaper, e2e green
- **0.7.59** mobile dispatch-confirm stack (full-width Dispatch, small Cancel above) + one-row
  chat header + gated feedback Send.
- **0.7.60** — **task push notifications FLATTEN markdown.** A Web Push body is PLAIN TEXT on
  every platform (not an Android limit); `notificationText()`.
- **0.7.61 — idle-task reaper.** The daily retention sweep auto-dismisses tasks untouched **>7d**
  AND tears down their linked agent pods. Env **`CLAWGATE_TASK_TTL`** (`off`/`0` disables);
  centralized in `dismissTask`.
- **0.7.62** markdown chat title + status icon removed from the chat header + sticky autoscroll.
- **0.7.63 — the SCROLL ROOT FIX.** The page is a fixed-height app shell (`h-dvh` +
  `overflow-hidden`) so **`#chat-log` is the ACTUAL scroller**.
  `min-h-dvh` let the WINDOW scroll → `logEl.scrollTop` was a silent no-op. Plus live tool/text interleave and a **task-detail
  modal** (tap the chat title → `/ui/agents/{name}/task` into `#task-modal`).
- **0.7.64 — STRUCTURED transcript.** A turn persists as ORDERED parts (text / tool_call /
  tool_result) via a **`PartCollector`** that OBSERVES the stream (**migration 0015**), so
  reconcile/reload show the FULL response with tool calls interleaved as collapsed chips — no
  more "only the final text segment" loss.
- **0.7.65** dismissing a task regroups the In-progress/Open/Done sections LIVE (broadcast
  `EventTaskChanged`).
- **0.7.66** full-height input, no gap — the input bar is now **IN-FLOW `flex-none`**, no more
  `fixed` + `pb-28` dead gap; scroll-to-working-bubble (`showWorking` → `forceScroll`); **drop
  the openclaw `NO_REPL` "no reply" sentinel** (`isSentinelReply` in render + `PartCollector`) —
  the gateway emits it when a turn ends via tool calls with nothing to say.
- **e2e SUITE GREENED** — 73 pass / 2 skip at the time, now **83 / 2**. See the core's e2e notes.

## 0.7.68–0.7.74
- **0.7.68** push-only `POST /api/notify` (notify-only, no approve/deny card).
- **0.7.69** re-synced the vendored kubeclaw chart 0.6.0 → **0.7.0** (fleet-hardening values).
- **0.7.72** source-provenance (**migration 0017**): `X-Clawgate-Source: claude-code` +
  `X-Clawgate-Session-Id` → `source_type` / `source_session_id` + a card chip.
- **0.7.73 TASK EDITING** — store `UpdateNote` partial-update + machine `PATCH /api/tasks/{id}` +
  a UI edit modal. Body/title/dispatch-config editable; status/provenance/created_at immutable;
  `in_progress` → 409. No migration.
- **0.7.74** machine status setter `PATCH /api/tasks/{id}/status` (hook-token; all statuses incl.
  `complete`; shared `applyTaskStatus` helper with the session path; broadcasts `task.changed`;
  no migration). The machine API now has full producer parity: create / read / edit / set-status
  / delete. 0.7.73 + 0.7.74 shipped and were verified live 2026-07-29.

## 0.7.75–0.7.79
- **0.7.75 task TAGS** as a routing key + the `title` companion fix (**migration 0018**:
  `tags TEXT[]` GIN-indexed + `title TEXT`). Normalized / sorted / deduped tags;
  `GET /api/tasks?tag=` AND-filter; `GET /api/tags` vocabulary. Full grammar and the reserved
  namespaces: `~/.claude/skills/clawgate/reference/internals.md`.
- **0.7.76** machine `DELETE /api/tasks/{id}`.
- **0.7.77** fixed the **markdown renderer re-parsing its own output** — see
  `~/.claude/skills/clawgate/reference/internals.md` (the vault fix, the 33.5s→0.7s quadratic, and the rule "fix the
  RENDERER, not the client").
- **0.7.78** machine `POST /api/tasks/{id}/comments`.
- **0.7.79** = two fixes. (a) `ui.taskTitle` **exported as `ui.TaskTitle`**
  (`internal/ui/notes.go:502`; `title` if non-blank, else `directory`) and
  `internal/api/agents.go`'s **two dispatch-label sites** now call it — `:158`
  `buildTaskDispatchView` (the dispatch-modal label) and `:268` `handleAgentNoteOptions` (the
  task combobox options) — so a `title`-bearing task stops being labelled by its `directory`;
  pinned by `internal/api/dispatch_label_test.go`. (b) The **JS-mirror sentinel leak is FIXED** —
  `agents_detail.go:843`'s md-link regex now carries the same sentinel exclusion its autolink
  always had. ⚠ **`internal/ui/markdown.go:51-54` still carries a STALE comment** asserting that
  JS leak is unfixed (0.7.79 never touched `markdown.go`) — don't trust it. Residual: a backtick
  inside a URL renders a truncated autolink plus literal text (no control byte in the href).

**Live pin as of 2026-08-13: 0.7.87** (`clawgatectl health`). Zach ships concurrently — this line is
stale by design; always re-check.
