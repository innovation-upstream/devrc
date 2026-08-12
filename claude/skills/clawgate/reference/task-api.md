# clawgate — machine (hook-token) Task API: the full producer surface

Read when: you are **writing or debugging a producer** that posts Tasks/cards into clawgate
(task-spec drafter, repo-cos, mail-actions, a script, the extension), or you got an unexpected
status code back from a machine endpoint.

Routes registered in `internal/api/server.go` `registerNotesRoutes`, handlers in
`internal/api/notes.go`. Token as `Authorization: Bearer <t>` **or** `X-Clawgate-Token: <t>`.

| op | route | notes |
|---|---|---|
| **create** | `POST /api/tasks` | `{directory, title, body, model, repo, branch, privileges, tags}`; `body` required (400); unknown keys silently dropped (no `DisallowUnknownFields`). ⚠ **Response is `{"id":N}` and nothing else** — not the created task; read any field back with `GET /api/tasks/{id}` |
| **read** | `GET /api/tasks[/{id}]` · `?tag=a&tag=b` | tag filter ANDs; bogus tag → `200 []`. ⚠ **NO `LIMIT`, no status filter** — returns the WHOLE board, newest-`updated_at` first, and `?status=`/`?limit=` are **silently ignored** (measured 0.7.85, 2026-08-11: 10 tasks, 94 KB, ~25k tokens, 57% of it comment bodies). Filter client-side — see "Reading the board cheaply" below. 🔑 **Comments are EMBEDDED in both the list and the single GET** (`comments: [{id,noteId,author,body,createdAt}]`) — there is **no read endpoint**: `GET /api/tasks/{id}/comments` is **405** (POST-only), which reads as "wrong method, keep looking" and sends you hunting for a route that was never there. Attachments are `omitempty` metadata only; bytes come from the session route `GET /tasks/{id}/attachments/{aid}` |
| **edit** | `PATCH /api/tasks/{id}` | content + dispatch config + `tags` (replace) / `addTags` / `removeTags` (merge); **status/provenance/created_at immutable**; `tags`+merge together → 400 (0.7.73/0.7.75). ⚠ **The `in_progress` 409 is REFINED, not blanket**: a **descriptive-tag-only** edit SUCCEEDS while in progress (a label is not a spec change). 409 fires only when a non-tag field is present, or any touched tag is a ROUTING tag — and a `tags` **replace** counts the CURRENT set as touched, so it 409s on any task that already has one |
| **set-status** | `PATCH /api/tasks/{id}/status` | ANY status incl. `complete`; **NO `in_progress` guard**; broadcasts `task.changed` + fires the `ready_for_review` push (0.7.74) |
| **delete** | `DELETE /api/tasks/{id}` | ⚠ shares `dismissTask`, so it **TEARS DOWN a live dispatched agent pod** (`Provisioner.Destroy`, background best-effort). **No in-progress guard — deliberately** (`TestAPITaskDeleteInProgressAllowed`). Delete a dispatched task only if you mean to kill its agent. `404` if absent — the existence probe is load-bearing, since `DELETE … WHERE id=$1` succeeds with 0 rows (0.7.76) |
| **comment (write)** | `POST /api/tasks/{id}/comments` | `{body}` only; author from the bounded `X-Clawgate-Source` allowlist (`{extension, api, drafter, repo-cos, claude-code}`, unknown → `api`), **NEVER from the body** — `user`/`operator` are structurally unreachable. Markdown; coalesced push on the machine path only (0.7.78) |
| **tag vocab** | `GET /api/tags` | `[{tag,count}]` |
| **projects** | `GET /api/projects` | ⚠ an **OBJECT**, not an array: `{"projects":[{"name","count"}]}` — and keyed `name`, unlike `/api/tags`'s `[{"tag","count"}]`. Derives from the `project:` tag namespace (0.7.81) |
| **merge** | `POST /tasks/merge` | 🔴 **SESSION route, no `/api` counterpart, FORM-encoded** (`winner=`,`loser=`). Merge by SUPERSEDE — nothing is deleted; winner gains the tag union + a comment, loser gets a comment and `complete`. 400 self-merge/bad id · 404 unknown · 409 if EITHER task is `in_progress` or already `complete`. 200 with an EMPTY body + `HX-Trigger`. The machine gap is deliberate: the audit comments are authored `user`, which `taskCommentAuthor` structurally cannot mint (0.7.84) |
| **push-only** | `POST /api/notify` | notify-only, no approve/deny card (0.7.68) |
| **provenance** | headers on create | `X-Clawgate-Source` + `X-Clawgate-Session-Id` → **stored** `source_type` / `source_session_id` + a card chip (0.7.72). The two headers are **independent** — source stamps with no session id, and vice versa. See the phantom-null note below before reporting provenance as broken |

Status vocabulary is exactly **`open` / `in_progress` / `ready_for_review` / `complete`**
(`notes.ValidStatus`) — there is **no `dismissed`**; dismissing deletes.

## ⚠ What each write actually returns (only `create` is the stingy one)
`POST /api/tasks` → `{"id":N}` and nothing else. But the others hand back a full object, so a
read-back `GET` is usually redundant:
- `PATCH /api/tasks/{id}` and `PATCH /api/tasks/{id}/status` → the **full updated task**.
- `POST /api/tasks/{id}/comments` → the **created comment** `{id,noteId,author,body,createdAt}`.
- `DELETE /api/tasks/{id}` → `{"id":N,"deleted":true}`.

## ⚠ Request keys ≠ response keys (the `sourceSessionId` trap, twice more)
`POST`/`PATCH` take **`branch`** and **`privileges`**; the task reads back as **`repoBranch`**
and **`grantProfiles`** (`notes.go`). Round-tripping a GET body into a PATCH silently drops both.
Both are `omitempty`, so on a task that never set them the key is simply absent — which looks
identical to "the field isn't wired". Confirm against `notes.go`, not against one task's JSON.

## Reading the board cheaply
The list GET returns everything, every time (~25k tokens today). Select before you read:
```bash
B=http://192.168.50.250:30302
H="Authorization: Bearer $(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)"

# board summary — ~360 tokens instead of ~27,200
curl -s -H "$H" "$B/api/tasks" | jq -r '.[] | "\(.id)\t\(.status)\t\(.title // .directory)\t\((.tags//[])|join(","))\t\((.comments//[])|length)c"'

# one task + its comments (comments are ALREADY here — there is no /comments GET)
curl -s -H "$H" "$B/api/tasks/161" | jq -r '"#\(.id) [\(.status)] \(.title)\n\n\(.body)\n\n--- comments ---", ((.comments//[])[] | "[\(.author) \(.createdAt[0:16])]\n\(.body)")'

# open work only (the server ignores ?status=)
curl -s -H "$H" "$B/api/tasks" | jq -r '.[] | select(.status=="open") | "\(.id)\t\(.title)"'
```
🔑 **A wrapper CLI was evaluated and rejected** (2026-08-11): ~99% of the saving above is the
`jq` selection, not the client — rendering buys only 12%, and the shorter invocation saves ~105
tokens against a ~27k payload. A binary would be a second thing to drift from the API, and a
stale binary returns confidently wrong output where a stale doc at least 405s at you.

**Route-scope distinction:** the session routes (`/tasks/...`, no `/api` prefix) are LAN/UI-only, and
the agent route `PATCH /agent/task/status` **forbids `complete`** — the machine
`PATCH /api/tasks/{id}/status` is the trusted-producer path that allows ALL statuses.

## ⚠ Provenance reads back as a phantom `null` — twice, for two different reasons
Both are reporting artifacts, not bugs. Measured against live **0.7.85**, 2026-08-09.

1. **You read it off the create response.** `handleAPITaskCreate` ends
   `writeJSON(w, 200, map[string]any{"id": note.ID})` (`internal/api/notes.go`), so `.sourceType`
   there is an **absent key**, not a null value — while the row is already stamped. `POST` with
   only `X-Clawgate-Source: claude-code` → `{"id":157}`, then `GET /api/tasks/157` →
   `"sourceType":"claude-code"`. **Verify provenance with a `GET`, never off the create.**
2. **You spelled the JSON key Go-style.** It is **`sourceSessionId`** — lowercase `d` —
   not `sourceSessionID` (`notes.Note` struct tags). The Go-style key silently reads `null`.

Also expected, not broken: the default/unidentified `api` source is stored as **NULL**, and both
fields are `omitempty`, so a plain post legitimately has no provenance keys at all. `claude-code`
has been in `taskSourceAllowlist` since the migration-0017 commit (0.7.72) — an unknown source
value collapses to `api` rather than erroring, which looks identical to "provenance isn't wired".

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
- **Hook-token endpoints** are `/api/send`, `/api/notify`, `/api/suggest`, `/api/response/{id}`,
  `/api/tasks*`, `/api/tags`, `/api/projects`. Secrets are NOT stored in the skill — retrieve it
  with `grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2`.
- 🔴 **The `/api/` prefix does NOT mean "needs the token."** `/api/requests`,
  `/api/openrouter/models`, `/api/push/*` and `/api/auto-approve*` sit behind the no-op
  `requireSession` and are **wide open on the LAN NodePort** — and `POST /api/auto-approve` is
  state-changing on the open side.
- 🔴🔴 **`POST /api/auto-approve-all` is the most consequential switch in the app, and it is
  unauthenticated on the LAN** (`internal/api/server.go:325`, wrapped in the no-op `requireSession`;
  handler at `:1105`). It accepts **either** a form body or JSON — `enabled` (`true`/`1`/`on`) plus
  `duration` (the UI offers 1h / 8h / 24h). Enabling it does **two** things, and the second is the
  one people miss:
  1. arms a **single global window** that auto-approves every *future* request **in every project**
     (not per-project — the server logs it as `auto-approve-ALL enabled … (FIREHOSE — every project)`);
  2. immediately **sweeps the existing pending queue**, approving everything already waiting.

  Runbook **checkpoints** (`store.TypeCheckpoint`) are skipped in the sweep — an approval gate stays
  a human decision. Disable with `enabled=false`, which clears the window and falls back to the
  per-project rules. e2e coverage: `e2e/tests/auto-approve-all.spec.ts` (2 tests, **not** Docker-gated).
- 🔴 **The idle-task reaper is a second unattended destroyer.** `defaultTaskReapAfter = 7d`
  (`internal/api/server.go:1229`); the daily retention sweep calls `reapIdleTasks` (`:1303`) which
  calls the **same `dismissTask`** as `DELETE /api/tasks/{id}` (`:1318`) — so an idle task's linked
  agent pod is torn down too. `CLAWGATE_TASK_TTL` is **unset in the live deployment** (verified
  2026-08-12), so the 7d default is what is running; `off` or `0` disables the reaper. Measured live 0.7.85, 2026-08-11: `GET /api/requests` → **200
  with no credential**, `GET /api/tasks` → **401**. Never infer exposure from the path prefix.
- 🔴 **`/operator/*` (11 routes) is a THIRD credential** — `requireOperatorToken` demands the
  reserved Operator *agent's* hooks token, not the hook token, which gets
  `401 {"error":"not the operator"}`.
- Everything else (the UI, `/ui/*`) is OPEN — behind Authelia publicly, directly reachable on the
  LAN. The hook never has a cookie; the UI never calls `/api/response/{id}`.
- 🔴 **"session auth" is not auth**: `requireSession` is a literal pass-through no-op since 0.7.37
  (`internal/api/auth.go`), so **everything on the LAN NodePort is unauthenticated — including
  `DELETE /tasks/{id}`**. And `requireHookToken` is **enforce-when-set**: with `CLAWGATE_HOOK_TOKEN`
  empty the machine endpoints are wide open too.
