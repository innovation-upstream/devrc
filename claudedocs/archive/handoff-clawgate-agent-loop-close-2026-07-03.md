# Handoff — clawgate: the agent loop CLOSES end-to-end (2026-07-02→03)

**One line:** a clawgate Task now goes card → pre-filled Dispatch → provisioned agent → **real PR** → `ready_for_review` → PWA done-push, with a phone-first operator UX. Shipped 0.7.44 → **0.7.58** (live). The load-bearing fix was a file-based git credential (openclaw's exec sandbox strips `$GITHUB_TOKEN`).

Operate everything via the **`/clawgate`** skill (updated this session) + the authoritative `homelab-talos/containers/clawgate/HANDOFF.md` (updated this session, see caveat below).

---

## What shipped (0.7.44–0.7.58, all live + verified)

- **Dispatch config on the Task** (migration **0014** `task_dispatch_config`): a Task carries `model`/`repo`/`repo_branch`/`grant_profiles`. From-card **Dispatch is a pre-filled confirm, not a blank form** (0.7.45). Machine reads **`GET /api/tasks[/{id}]`** (0.7.44). Both producers (repo-cos `clawgate.py` + the drafter) fill the config on POST. Default model = **deepseek** ("I'll override if needed").
- **🔴 Close-the-loop (0.7.48–0.7.49):** repo-backed TASK agents branch→push→open a PR (REST API; `gh` not in image)→comment→`ready_for_review`. **The fix that made push work:** openclaw's exec sandbox STRIPS `$GITHUB_TOKEN` from the agent's shell — so the git helper reads a FILE `/root/.gh-token` written at pod startup (`internal/agents/values.go buildHelmValues` extraInitCommands). Proven: container `printenv`=40 chars, agent's own=0. **Verified live: PR #49.**
- **Tasks UI:** [Requests|Tasks] segmented tab + horizontal swipe; pending-count badge; grouped In-progress/Open/Done; **live dispatch feedback** (`agent.changed` SSE → status chip + Open-chat CTA, no reload); optimistic auto-approve; relative timestamp; **discoverable dismiss** that **tears down the linked agent pod** (0.7.56).
- **Agent chat:** LIVE kickoff streaming (`agent.stream` SSE + shared WS/SSE renderer, 0.7.50+0.7.57); completion banner (Mark complete → /tasks); working indicator; smaller header / model-name-only / repo chip; markdown code-protection + clickable bare URLs.
- **PWA task-lifecycle push (0.7.53):** created (4s-debounce coalesced) / provisioned / done. Decoupled via `SetStatusChangeHook`/`SetChatReplyHook`/`SetStreamHook` + `Broadcast*` (agents pkg stays free of api/push).

Gate every release: `go build/vet/test -race` + **e2e 70 pass / 2 skip** (flaky `agent-selfservice.spec.ts:33` = pg cold-start) + hook bats.

## Docs/skills updated this session (the actual ask)
- **`~/.claude/skills/clawgate/SKILL.md`** — version 0.7.42→0.7.58, dense 0.7.44–0.7.58 arc, migrations →0014, e2e 61→70, VER hint →0.7.59 + collision warning, and the Repos-tab git-credential fact rewritten to the file-based `/root/.gh-token` + exec-sandbox-strip gotcha.
- **`~/.claude/skills/repo-cos/SKILL.md`** — the approve→Task line now notes the payload carries the resolved `owner/name` repo + deepseek model (migration 0014).
- **`homelab-talos/containers/clawgate/HANDOFF.md`** — header/status/next-version →0.7.58/0.7.59, a new 0.7.44–0.7.58 section, footer e2e/version, and the loop-CLOSED reframing of the #1 soak item.
- **Memories written this session:** `openclaw-exec-sandbox-strips-env`, `task-dispatch-default-deepseek`, `clawgate-version-before-build`, `agent-pods-flux-suspended`.

⚠ **HANDOFF.md is uncommitted in the homelab-talos working tree** (that repo's tree is permanently dirty; the deploy pipeline stages HANDOFF.md with the clawgate paths). It'll land with the **next** clawgate deploy commit — if the next change is unrelated, stage it explicitly so the doc update isn't orphaned.

## Gotchas that bit (don't relearn)
- **Version-collision:** `git fetch origin trunk` + check the LIVE deployment pin BEFORE numbering a release — Zach ships concurrently and Harbor tags are mutable (clobbered his 0.7.47 once). Memory `clawgate-version-before-build`.
- **Secret env is stripped in the agent sandbox** — prefer a startup-written FILE over an env var for any agent-run secret. Memory `openclaw-exec-sandbox-strips-env`.
- **agent-pods Flux kustomization is SUSPENDED** → drafter configmap/cronjob edits are `kubectl apply`'d surgically, not GitOps'd. Memory `agent-pods-flux-suspended`.
- `g.If` gomponents args eval EAGERLY → nil-guard maybe-nil values.
- "notes" = internal name for user-facing "Tasks" (pkg `internal/notes`, table `notes`, `/tasks`→`handleNotesContent`).

## Next / open
1. **SOAK — the build is done, the value is in USE.** The loop is closed (PR #49 proved it); the real test is Zach adjudicating + dispatching drafter cards over ≥3 real runs. Don't add loop features until a few cycles show it earns its place.
2. **#3 (unstarted):** fold the dead Suggestions tab into the queue as a generative SOURCE (session-end → drafts next task-spec → `POST /api/tasks`), NOT a standalone feature. Plan in `close-the-loop` STATE.md.
3. A deterministic server-side `agent_open_pull_request` native tool would beat a weak model hand-rolling the REST call (noted in `openclaw-exec-sandbox-strips-env`).
4. Orphan sweep done this session (deleted `sharp-wren`, task #51 complete); only the `#10` operator + unrelated old devpods remain.
