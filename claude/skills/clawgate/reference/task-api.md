# clawgate — machine (hook-token) Task API: the full producer surface

Read when: you are **writing or debugging a producer** that posts Tasks/cards into clawgate
(task-spec drafter, repo-cos, mail-actions, a script, the extension), or you got an unexpected
status code back from a machine endpoint.

Routes registered in `internal/api/server.go` `registerNotesRoutes`, handlers in
`internal/api/notes.go`. Token as `Authorization: Bearer <t>` **or** `X-Clawgate-Token: <t>`.

| op | route | notes |
|---|---|---|
| **create** | `POST /api/tasks` | `{directory, title, body, model, repo, branch, privileges, tags}`; `body` required (400); unknown keys silently dropped (no `DisallowUnknownFields`) |
| **read** | `GET /api/tasks[/{id}]` · `GET /api/tasks?tag=a&tag=b` | tag filter ANDs; bogus tag → `200 []`, not an error |
| **edit** | `PATCH /api/tasks/{id}` | content + dispatch config + `tags` (replace) / `addTags` / `removeTags` (merge); **status/provenance/created_at immutable**; **409 if `in_progress`**; `tags`+merge together → 400 (0.7.73/0.7.75) |
| **set-status** | `PATCH /api/tasks/{id}/status` | ANY status incl. `complete`; **NO `in_progress` guard**; broadcasts `task.changed` + fires the `ready_for_review` push (0.7.74) |
| **delete** | `DELETE /api/tasks/{id}` | ⚠ shares `dismissTask`, so it **TEARS DOWN a live dispatched agent pod** (`Provisioner.Destroy`, background best-effort). **No in-progress guard — deliberately** (`TestAPITaskDeleteInProgressAllowed`). Delete a dispatched task only if you mean to kill its agent. `404` if absent — the existence probe is load-bearing, since `DELETE … WHERE id=$1` succeeds with 0 rows (0.7.76) |
| **comment** | `POST /api/tasks/{id}/comments` | `{body}` only; author from the bounded `X-Clawgate-Source` allowlist (`{extension, api, drafter, repo-cos, claude-code}`, unknown → `api`), **NEVER from the body** — `user`/`operator` are structurally unreachable. Markdown; coalesced push on the machine path only (0.7.78) |
| **tag vocab** | `GET /api/tags` | `[{tag,count}]` |
| **push-only** | `POST /api/notify` | notify-only, no approve/deny card (0.7.68) |
| **provenance** | headers on create | `X-Clawgate-Source` + `X-Clawgate-Session-Id` → `source_type` / `source_session_id` + a card chip (0.7.72) |

Status vocabulary is exactly **`open` / `in_progress` / `ready_for_review` / `complete`**
(`notes.ValidStatus`) — there is **no `dismissed`**; dismissing deletes.

**Route-scope distinction:** the session routes (`/tasks/...`, no `/api` prefix) are LAN/UI-only, and
the agent route `PATCH /agent/task/status` **forbids `complete`** — the machine
`PATCH /api/tasks/{id}/status` is the trusted-producer path that allows ALL statuses.

## ⚠ Tags are hard-validated — one bad tag breaks the whole create
An invalid tag or an unknown `runbook:` is a hard 400. Grammar in one line: lowercased, ≤20 tags,
≤64 runes each, charset `[a-z0-9._/-]`, at most one `:`, no empty half. Reserved namespaces are a
CLOSED set — `runbook:` (hard-validated), `initiative:` (soft), `gate:` (**blocks dispatch, 409**),
`auto:dispatch` (off).

🔴 That **400 is a load-bearing WIRE CONTRACT** — producers key their fail-open retry on it. Full
grammar, rationale, and the `title`-vs-`directory` rules:
`/home/zach/workspace/devrc/claude/skills/clawgate/reference/internals.md`.

## ⚠ A task body may contain element references from the browser extension
Lines like
`` - `#remix-row-cf23 > div.flex > button.grid` — button "Delete remix" (host/path · prev · next) ``.
🔑 **Do NOT search the selector first** (it is the least durable signal): work domain/path →
adjacent text → selector → accessible name. Full procedure, worked example, and the
`buildEnrichment` developer note:
`/home/zach/workspace/devrc/claude/skills/clawgate/reference/element-references.md`.

## Card producers — all share `CLAWGATE_HOOK_TOKEN`
⚠ **Rotation coupling: rotating the token means updating all three, or they fail silently.**
1. The two local hooks (token in `~/.claude/clawgate.env`).
2. The **task-spec drafter** — a homelab kubeclaw CronJob POSTing one daily `type:"permission"`
   digest card to `/api/send`, tool=`Task-spec drafter`, project=`task-drafter-agent`. It reads the
   token from homelab secret **`task-drafter-agent-secrets`** (ns `devpod-task-drafter`), key
   `CLAWGATE_HOOK_TOKEN` — **miss this on a rotation and the daily digest 401s silently.** A daily
   `Task-spec drafter` card is the drafter, NOT a real CC permission prompt. See the
   `close-the-loop` skill's STATE.md.
3. **repo-cos** (`devrc/scripts/repo-cos/clawgate.py`) — on an "approve" reply it POSTs the proposal
   as a durable Task via `POST /api/tasks`. Reads the token from **`~/.claude/clawgate.env`** on the
   workbench (NOT a k8s secret). See the `repo-cos` skill.

## Auth / access (0.7.37 — clawgate has NO human auth of its own)
No magic-link `/login?token=`, no session cookie, no `CLAWGATE_AUTH_TOKEN` /
`CLAWGATE_SESSION_SECRET` (both now orphaned-unused in `clawgate-secrets`), no Traefik basic-auth,
and **no login QR to manage**.

- **Phone / public** → `https://clawgate.zacx.dev`, pass the **Authelia passkey** at
  `https://login.zacx.dev` (user `zach`, already enrolled). Authelia owns auth/SSO now — manage it
  there, not in clawgate. Memory `authelia-passkey-sso`.
- **LAN** → `http://192.168.50.250:30302` or `clawgate.workbench.lan` — open, no auth.
- **Machine endpoints** (`/api/send`, `/api/tasks`, `/api/notify`, `/api/response/{id}`) require the
  `CLAWGATE_HOOK_TOKEN` bearer. Secrets are NOT stored in the skill — retrieve it with
  `grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2`.
- Everything else (the UI, `/ui/*`) is OPEN — behind Authelia publicly, directly reachable on the
  LAN. The hook never has a cookie; the UI never calls `/api/response/{id}`.
- 🔴 **"session auth" is not auth**: `requireSession` is a literal pass-through no-op since 0.7.37
  (`internal/api/auth.go`), so **everything on the LAN NodePort is unauthenticated — including
  `DELETE /tasks/{id}`**. And `requireHookToken` is **enforce-when-set**: with `CLAWGATE_HOOK_TOKEN`
  empty the machine endpoints are wide open too.
