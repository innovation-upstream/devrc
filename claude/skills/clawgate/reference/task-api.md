# clawgate — machine (hook-token) Task API: the full producer surface

Read when: you are **writing or debugging a producer** that posts Tasks/cards into clawgate
(task-spec drafter, repo-cos, mail-actions, a script, the extension), or you got an unexpected
status code back from a machine endpoint.

Routes registered in `internal/api/server.go` `registerNotesRoutes`, handlers in
`internal/api/notes.go`. Token as `Authorization: Bearer <t>` **or** `X-Clawgate-Token: <t>`.

| op | route | notes |
|---|---|---|
| **create** | `POST /api/tasks` | `{directory, title, body, model, repo, branch, privileges, tags}`; `body` required (400); unknown keys silently dropped (no `DisallowUnknownFields`). ⚠ **Response is `{"id":N}` and nothing else** — not the created task; read any field back with `GET /api/tasks/{id}` |
| **read** | `GET /api/tasks[/{id}]` · `?tag=&status=&limit=&summary=` | ⚠ **RE-MEASURED against live 0.7.87 (2026-08-13): `?status=`, `?limit=` and `?summary=1` are now honoured SERVER-SIDE.** The older claim here that they were "silently ignored" was true at 0.7.85 and is now WRONG — do not re-derive it. Positive control run today: unfiltered `19` tasks vs `--limit 3` → `3`; `--status open` returned only `open`. `tag` ANDs (repeatable), bogus tag → `200 []`; default order newest-`updated_at` first. Unfiltered+unsummarised is still ~25k tokens, so **always pass `--summary` first** (drops bodies/comments/attachments for counts). 🔑 **Comments are EMBEDDED in both the list and the single GET** (`comments: [{id,noteId,author,body,createdAt,retracted?}]`) — there is **no read endpoint**: `GET /api/tasks/{id}/comments` is **405** (POST-only), which reads as "wrong method, keep looking" and sends you hunting for a route that was never there. 🔴 **A retracted comment still appears here with `body: ""` and `"retracted": true`** — see "Comment retraction" below before reading an empty body as a bug. Attachments are `omitempty` metadata only; bytes come from the session route `GET /tasks/{id}/attachments/{aid}` |
| **edit** | `PATCH /api/tasks/{id}` | content + dispatch config + `tags` (replace) / `addTags` / `removeTags` (merge); **status/provenance/created_at immutable**; `tags`+merge together → 400 (0.7.73/0.7.75). ⚠ **The `in_progress` 409 is REFINED, not blanket**: a **descriptive-tag-only** edit SUCCEEDS while in progress (a label is not a spec change). 409 fires only when a non-tag field is present, or any touched tag is a ROUTING tag — and a `tags` **replace** counts the CURRENT set as touched, so it 409s on any task that already has one |
| **set-status** | `PATCH /api/tasks/{id}/status` | ANY status incl. `complete`; **NO `in_progress` guard**; broadcasts `task.changed` + fires the `ready_for_review` push (0.7.74) |
| **delete** | `DELETE /api/tasks/{id}` | ⚠ shares `dismissTask`, so it **TEARS DOWN a live dispatched agent pod** (`Provisioner.Destroy`, background best-effort). **No in-progress guard — deliberately** (`TestAPITaskDeleteInProgressAllowed`). Delete a dispatched task only if you mean to kill its agent. `404` if absent — the existence probe is load-bearing, since `DELETE … WHERE id=$1` succeeds with 0 rows (0.7.76) |
| **comment (write)** | `POST /api/tasks/{id}/comments` | `{body}` only; author from the bounded `X-Clawgate-Source` allowlist (`{extension, api, drafter, repo-cos, claude-code}`, unknown → `api`), **NEVER from the body** — `user`/`operator` are structurally unreachable. 🔴 **`clickup` is a SIXTH member in trunk SOURCE (PR #346, 2026-08-19) that is NOT in any released image** — probed live on **0.7.96**, `X-Clawgate-Source: clickup` still comes back `author: "api"`. This is the skill drifting in BOTH directions at once, so treat the set as *five live, six in source* until a release lands, and **probe rather than assume**: post a throwaway comment and read the author back. A producer that keys an echo guard on `author == "clickup"` today is silently inert — clickup-mirror keys on its own ledger for exactly this reason. This entry is the authoritative copy; `internals.md` defers to it. Markdown; coalesced push on the machine path only (0.7.78). ⚠ **The body is `strings.TrimSpace`d before storage**, so it comes back shorter than you sent it whenever it had surrounding whitespace — normalisation, NOT truncation. The real cap is `maxTaskBodyLen` = **200,000 runes** (truncate-never-reject). `clawgatectl` ≤ 2026-08-15 misreported the trimmed newline every heredoc/`--body-file` produces as *"sent N runes, stored N-1 … not recoverable"* (homelab-infra #325) |
| **comment (delete)** | `DELETE /api/tasks/{id}/comments/{cid}` | 🔴 **SOFT — it redacts, it does not remove** (migration 0021, shipped 0.7.90). Sets `note_comments.deleted_at`; the ROW SURVIVES and still appears in both embeds with `body: ""` + `"retracted": true`. Recoverable by a one-column `UPDATE`. Task-scoped: a `cid` on a different task is a 404, not a cross-thread hit. **Idempotent-by-404** — a repeat is `404 {"error":"comment not found"}`, exactly as a repeat task delete answers. Bad id → 400 · unknown/**dismissed** task → 404 `task not found` · store failure → 500. Broadcasts `task.changed`. There is **no bulk/undelete route** |
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
- `DELETE /api/tasks/{id}/comments/{cid}` → `{"id":<cid>,"taskId":N,"deleted":true}`.

## 🔴 Comment retraction: an empty `body` is a TOMBSTONE, not a blanking bug
Verified live on 0.7.95 (2026-08-15) against a throwaway task. Deleting a comment leaves it in
place, redacted:

```json
{ "id": 105, "noteId": 205, "author": "claude-code", "body": "", "retracted": true }
```

**Why the row survives rather than disappearing.** A dismissed TASK vanishing from the board is
visible — the card is gone. A comment vanishing from a thread would be **invisible**: the thread
just silently shortens, and on a board agents read as authoritative a quiet mass retraction would
leave no trace. So the reads REDACT rather than filter (`commentCols` in
`internal/notes/pgstore.go` selects `CASE WHEN deleted_at IS NULL THEN body ELSE '' END` plus the
flag). The body is never selected out of the database at all, so no read path can leak it.

🔴 **`retracted` is the predicate — never infer retraction from an empty body**, and never treat an
empty body on a live comment as a retraction. The field is `omitempty`, so a live comment simply
has no `retracted` key.

⚠ **This is the trap that makes the soft delete look broken.** A consumer that prints `.body` per
comment (the bare `jq` recipe below used to) renders a retraction as a blank, unexplained entry —
which reads as "DELETE returned 200 and blanked the body". It didn't; it retracted it. Check the
flag before filing a bug. The UI already draws a `comment retracted` tombstone in its place, on both
the task card and the agent detail page.

## 🔴 Notifications: a COMMENT is the only write that notifies a watcher
Read in source on `trunk`, 2026-08-14 — not inferred from behaviour:

| write | pushes? | site |
|---|---|---|
| `POST /api/tasks/{id}/comments` | **always**, coalesced, **machine path only** | `notifyTaskCommented`, `internal/api/notes.go:1414` (handler `handleAPITaskComment`, `:1354`) |
| `PATCH /api/tasks/{id}/status` | **only on ENTERING `ready_for_review`**, deduped | `notifyTaskDone` from `applyTaskStatus`, `notes.go:1065`; impl `internal/api/push_task.go:214` |
| `PATCH /api/tasks/{id}` (edit) | no | — |
| `POST /api/tasks` (create) | no task push — the card paths are `/api/send` and `/api/notify` | — |

What a producer (and any agent picking up a task) must design around:
- 🔴 **Flipping a task to `in_progress` notifies NOBODY.** Any "I have started" signal has to be a
  COMMENT. This is exactly why the skill's pickup ritual posts a **pre-start comment before** the
  `in_progress` flip: it is the watcher's only chance to object before the work happens.
- The **session** (human) comment route deliberately does not push — that is Zach typing, and his own
  phone must not buzz; the per-agent route keeps its existing no-push behaviour. Only the machine
  `/api/tasks/{id}/comments` path buzzes.
- `notifyTaskDone` is deduped per task, and the mark is CLEARED whenever the task leaves
  `ready_for_review` — so `ready → in_progress → ready` notifies again, while a no-op re-save does not.

**Comments are exempt from the `in_progress` 409.** `POST /api/tasks/{id}/comments` is a different
route with no in-progress guard, while `PATCH /api/tasks/{id}` 409s once in progress on any non-tag
field or any routing tag. So an agent recording derived acceptance criteria, a plan or an assumption
against an in-progress task **comments them — it never PATCHes the body.** Three reasons, all
load-bearing: it dodges the 409, it leaves the author's text untouched, and it keeps provenance
unambiguous (body = the author's words, comments = the agent's).

🔴 **`complete` is structurally out of reach for a dispatched devpod agent.**
`PATCH /agent/task/status` gates on `notes.StatusAllowedForAgent` → `taskstatus.AllowedForAgent` =
`Valid(s) && s != Complete` (`internal/taskstatus/taskstatus.go:79`), so a devpod agent's terminal
state is always `ready_for_review`. Only the machine `PATCH /api/tasks/{id}/status` — the LOCAL
pickup path — can set `complete`, so the skill's author-specified-criteria gate governs that
permission and has no bearing whatsoever on the devpod route.

## ⚠ Request keys ≠ response keys (the `sourceSessionId` trap, twice more)
`POST`/`PATCH` take **`branch`** and **`privileges`**; the task reads back as **`repoBranch`**
and **`grantProfiles`** (`notes.go`). Round-tripping a GET body into a PATCH silently drops both.
Both are `omitempty`, so on a task that never set them the key is simply absent — which looks
identical to "the field isn't wired". Confirm against `notes.go`, not against one task's JSON.

## `clawgatectl` — the machine client (use it instead of curl)

🔑 **SUPERSEDES the 2026-08-11 note here that "a wrapper CLI was evaluated and rejected".** That
note has been deleted: `clawgatectl` shipped on 2026-08-12 in
`containers/clawgate/cmd/clawgatectl`, and `devrc/nix/pkgs/tools/clawgatectl.nix` puts it on PATH
on both hosts. Its argument against a binary ("a stale binary returns confidently wrong output")
is answered structurally, not by promising to keep it fresh: response bodies are passed to stdout
**verbatim**, never re-marshalled, so a field a newer server adds survives; and every command
prints a one-line `note: server X, clawgatectl built for Y` skew warning **to stderr** when the two
disagree. What is NOT answered: a route the CLI has never heard of is still a route you must curl.

🔴 **That skew note was MUTE for 19 hours on the laptop, and the argument above was wrong until
2026-08-18.** `clawgatectl.nix` used to carry a hand-written `version` literal and stamp it with
`-ldflags -X main.buildVersion=`, so on 2026-08-14 the laptop — whose `~/workspace/homelab-talos`
was 24 commits behind — built a CLI with **no `task status` and no `task comment`** and the nix
literal labelled it `0.7.95` anyway. `h.Version == buildVersion` compared equal, the note stayed
silent, and `clawgatectl task status <id> in_progress` printed help and **exited 0**. The fix is
in devrc: the version is now **read out of
`<homelab-talos>/containers/clawgate/cmd/clawgatectl/client.go`'s own `var buildVersion`**,
so a stale checkout reports its real version and the skew note fires correctly. Two consequences
worth knowing: **the note is only as good as the checkout's freshness** — `drift-check.sh` rc 17
now reports a stale `homelab-talos` per host — and an **unparseable** `var buildVersion` line means
the package is not installed at all (`clawgatectl: command not found`), never a guessed label.

It reads `CLAWGATE_API_URL` + `CLAWGATE_HOOK_TOKEN` out of `~/.claude/clawgate.env` itself, so the
`H="Authorization: Bearer $(grep '^CLAWGATE_HOOK_TOKEN=' … | cut -d= -f2)"` preamble that used to
head every recipe in this skill **is gone** — the token never reaches argv (`/proc` is world
readable) and never reaches your scrollback.

**Commands that exist. There are no others** — anything else is still curl:

| command | route |
|---|---|
| `clawgatectl health` | `GET /health` (open, no token) |
| `clawgatectl agent ls` | `GET /api/agents` |
| `clawgatectl agent resolve <name> [--id]` | `GET /api/agents`, matched client-side |
| `clawgatectl task ls [--tag --status --limit --summary]` | `GET /api/tasks` |
| `clawgatectl task get <id>` | `GET /api/tasks/{id}` |
| `clawgatectl task create --body\|--body-file [--title --tag --repo --branch --model --directory --privilege]` | `POST /api/tasks` |
| `clawgatectl task status <id> <open\|in_progress\|ready_for_review\|complete>` | `PATCH /api/tasks/{id}/status` |
| `clawgatectl task comment <id> --body\|--body-file [--source]` | `POST /api/tasks/{id}/comments` |

```bash
# board summary, filtered SERVER-side (MEASURED live 0.7.87, 2026-08-13: 19 tasks unfiltered, 3 with --limit 3)
clawgatectl task ls --summary --status open --limit 20 \
  | jq -r '.[] | "\(.id)\t\(.status)\t\(.title // .directory)\t\((.tags//[])|join(","))\t\(.commentCount)c"'

# one task + its comments (comments are ALREADY here — there is no /comments GET)
# NOTE the `.retracted` branch: without it a retracted comment prints as a blank
# entry and reads as a server bug. See "Comment retraction" above.
clawgatectl task get 177 \
  | jq -r '"#\(.id) [\(.status)] \(.title)\n\n\(.body)\n\n--- comments ---", ((.comments//[])[] | "[\(.author) \(.createdAt[0:16])]\n\(if .retracted then "(comment retracted)" else .body end)")'

# name -> id: THE read that replaces `SELECT id FROM agents WHERE name=…`
clawgatectl agent resolve operator --id       # -> 10
clawgatectl task create --title t --body b    # -> {"id":183}
```

**stdout is JSON and nothing else**; diagnostics, skew notes and resolve candidates go to stderr,
so `| jq` can never be corrupted. Exit codes are the contract — all MEASURED against live 0.7.87
on 2026-08-13, none inferred:

| rc | meaning | measured trigger |
|---|---|---|
| 0 | ok | `health` → `{"status":"ok","version":"0.7.87"}` |
| 1 | server error (5xx) | — |
| 2 | usage / **empty path parameter** | `task get ""` → refuses to send, no request leaves the host (an empty id makes `/api/tasks/` → 301 to a DIFFERENT route) |
| 3 | auth: clawgate rejected the token (**JSON** 401/403) | `--token wrong-token agent ls` |
| 4 | not found on a known route | `task get 999999` → `task not found`; `agent resolve nope` → candidates on stderr |
| 5 | conflict (409) | e.g. a non-tag `PATCH` on an `in_progress` task |
| 6 | network unreachable / timeout | — |
| 7 | route absent (router's own text/plain 404) → this CLI is newer than the server | — |
| 8 | **non-JSON where JSON was expected** | `--api-url https://clawgate.zacx.dev agent ls` |

🔴 **clawgatectl is LAN-first, and the public host fails in a shape the design did not predict.**
Against `https://clawgate.zacx.dev` with `Accept: application/json`, Authelia answers
**`401` + `Content-Type: text/html`** and a cross-host `Location:` — *not* the 302 a plain curl
(sending `Accept: */*`) gets. So the discriminator is the **body type, not the status**: a non-JSON
401/403 means an edge gate answered and you get **exit 8** with "use the LAN URL"; a **JSON**
401/403 means clawgate itself rejected the token and you get **exit 3**. Confusing the two sends
you hunting a credential that is fine. `requireSession` needs an interactive passkey — it cannot be
scripted at all.

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
⚠ **Rotation coupling: rotating the token means updating all three of 1–3 below, or they fail
silently.** (4 is exempt — it reads clawgate's own secret rather than holding a copy.)
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
4. **clickup-mirror** (`homelab-talos/scripts/clickup-mirror/`, CronJob in
   `clusters/workbench/apps/clickup-mirror/`) — mirrors Zach's assigned ClickUp tickets into Tasks
   and writes agent progress back. 🔴 **The first producer that is not write-only**: it also
   `GET`s `/api/tasks`, `PATCH`es content/tags/status and POSTs comments, so it is a *consumer* of
   this whole surface, not just `POST /api/tasks`. Two consequences worth knowing before you change
   a route's semantics: it depends on **comments being EMBEDDED** in the task read (there is no
   comment GET), and on a **descriptive-tag-only `PATCH` succeeding while `in_progress`**.
   Correlation key is the descriptive tag **`clickup:<ticket-id>`** (+ `clickup-list:<slug>`); a
   durable ledger in Postgres schema `clickup_mirror` (inside clawgate's own DB) is what stops a
   dismissed ticket being resurrected — **it deliberately does NOT dedupe by tag**, so tasks created
   outside that ledger get duplicated on the next run. Reads `CLAWGATE_HOOK_TOKEN` from clawgate's
   **own `clawgate-secrets`** (same namespace, never copied — hence exempt from the rotation
   coupling above). As of 2026-08-19 it ships **suspended**, dry-run, with write-back disabled.

## Auth / access (0.7.37 — clawgate has NO human auth of its own)
No magic-link `/login?token=`, no session cookie, no `CLAWGATE_AUTH_TOKEN` /
`CLAWGATE_SESSION_SECRET` (both now orphaned-unused in `clawgate-secrets`), no Traefik basic-auth,
and **no login QR to manage**.

- **Phone / public** → `https://clawgate.zacx.dev`, pass the **Authelia passkey** at
  `https://login.zacx.dev` (user `zach`, already enrolled). Authelia owns auth/SSO now — manage it
  there, not in clawgate. Memory `authelia-passkey-sso`.
- **LAN** → `http://192.168.50.250:30302` or `clawgate.workbench.lan` — open, no auth.
- **Which endpoints take the hook token is now enumerated exhaustively** — see "The complete
  machine surface" at the bottom of this file, derived from the checked-in route golden. Never
  hand-list it from memory: this bullet used to name seven routes out of fifteen, and that partial
  list is how `GET /api/agents` stayed invisible for months.
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
- 🔴 **`/operator/*` is a THIRD credential** — `requireOperatorToken` demands the reserved Operator
  *agent's* hooks token, not the hook token, which gets `401 {"error":"not the operator"}`. It
  covers **11 of the 13** `/operator*` routes; `GET /operator` and `POST /operator/provision` are
  `requireSession`, i.e. open on the LAN.
- Everything else (the UI, `/ui/*`) is OPEN — behind Authelia publicly, directly reachable on the
  LAN. The hook never has a cookie; the UI never calls `/api/response/{id}`.
- 🔴 **"session auth" is not auth**: `requireSession` is a literal pass-through no-op since 0.7.37
  (`internal/api/auth.go`), so **everything on the LAN NodePort is unauthenticated — including
  `DELETE /tasks/{id}`**. And `requireHookToken` is **enforce-when-set**: with `CLAWGATE_HOOK_TOKEN`
  empty the machine endpoints are wide open too.

---

## The complete machine surface — all 22 `/api/*` routes, with auth

🔴 **Read this before concluding a capability does not exist.** The core SKILL.md used to name
three clawgate routes (`/health`, `/api/send`, `POST /agents`) out of **118 registered**. That gap
is not cosmetic: it is why `GET /api/agents` went unnoticed and someone reached into Postgres with
`SELECT id FROM agents WHERE name=…` for a lookup one authenticated GET already answered.

**Source of truth: `containers/clawgate/internal/api/testdata/routes.golden`** — 118 routes, checked
in, diffed by `TestRoutesMatchGolden` against what `registerRoutes` actually registers, so a route
added/removed/re-verbed cannot land without a human eyeballing the diff. Regenerate with
`UPDATE_ROUTES_GOLDEN=1 go test ./internal/api -run TestRoutesMatchGolden`.

🔴 **The golden records PATTERNS ONLY — it is BLIND to auth.** By the time a handler reaches the
mux it is already wrapped, so `requireHookToken(h)` and `requireSession(h)` are the same type and
the gate cannot tell them apart. **A route silently moving between wrappers keeps that test green.**
The auth column below therefore comes from the registration sites in the code, not from the golden,
and must be re-read there — it is a snapshot, and the golden will not catch it going stale.

**There are FOUR wrappers, not two** (`routes_golden_test.go`; `server.go`'s own comment claimed
two until it was corrected):

| wrapper | where | what it actually enforces |
|---|---|---|
| `requireSession` | `auth.go:40` | 🔴 **NOTHING — the body is literally `return next`.** A pass-through since 0.7.37 |
| `requireHookToken` | `auth.go:49` | machine bearer (`Authorization: Bearer` or `X-Clawgate-Token`), **enforce-when-set**: an empty `CLAWGATE_HOOK_TOKEN` opens it |
| `requireOperatorToken` | `operator.go:58` | bearer must be the reserved agent named `Operator` |
| `requireAgentToken` | `agent.go:30` | bearer resolves to *any* agent; that agent is injected into the request ctx |

### `requireHookToken` — 16 routes (what clawgatectl and every producer use)
<!-- COUNT RE-DERIVED from source on trunk 2026-08-15: 14 registrations in server.go
     + `GET /api/agents` (agents.go:50) + `POST /api/suggest` (suggest.go:30) = 16.
     Was 15 before the comment-delete route (0.7.90). -->

| route | clawgatectl | note |
|---|---|---|
| `GET /api/agents` | `agent ls` / `agent resolve` | the roster; **the Postgres-lookup killer** |
| `GET /api/tasks` | `task ls` | `?tag= &status= &limit= &summary=1`, all server-side at 0.7.87 |
| `GET /api/tasks/{id}` | `task get` | comments embedded |
| `POST /api/tasks` | `task create` | returns `{"id":N}` only |
| `PATCH /api/tasks/{id}` | — curl | content/dispatch/tags; refined `in_progress` 409 |
| `PATCH /api/tasks/{id}/status` | `task status` | any status incl. `complete`; no in-progress guard. The CLI validates the status client-side (exit 2, nothing sent) because the server's 400 is the bare `invalid or missing status` and never names the four |
| `DELETE /api/tasks/{id}` | — curl | ⚠ tears down a live agent pod |
| `POST /api/tasks/{id}/comments` | `task comment` | author from `X-Clawgate-Source`, never the body — so the CLI has **no `--author` flag**. `--source` defaults to `claude-code`; an unallowlisted value is not an error, it is silently authored as `api`, so the CLI compares the returned author against the requested source and warns on **stderr** |
| `DELETE /api/tasks/{id}/comments/{cid}` | — curl | 🔴 **SOFT delete** (0.7.90) — redacts to `body:""` + `retracted:true`, row survives, idempotent-by-404. Its session twin `DELETE /tasks/{id}/comments/{cid}` is **open on the LAN**, deliberately (the browser holds no token) |
| `GET /api/tags` | — curl | `[{tag,count}]` |
| `GET /api/projects` | — curl | ⚠ an OBJECT `{"projects":[…]}`, keyed `name` |
| `POST /api/send` | — curl | the approval card the PermissionRequest hook posts |
| `POST /api/notify` | — curl | push-only, no approve/deny card |
| `POST /api/suggest` | — curl | the Stop hook's "Suggested next step" ingest |
| `GET /api/response/{id}` | — curl | the hook's decision poll |
| `DELETE /api/response/{id}` | — curl | the hook's cleanup |

### `requireSession` — 7 `/api/*` routes that are **WIDE OPEN on the LAN NodePort**
`GET /api/requests` · `GET /api/openrouter/models` · `GET /api/push/vapid-public-key` ·
`POST /api/push/subscribe` · `POST /api/push/unsubscribe` · `POST /api/auto-approve` ·
🔴🔴 `POST /api/auto-approve-all` (the firehose — see above).

### The other 96 routes
Not `/api/*` and mostly not machine-facing: `/ui/*` htmx fragments, the page routes, `/agents*`
(dispatch, **form-encoded**, `requireSession`), `/tasks/*` session routes (incl. `POST /tasks/merge`,
which has **no `/api` counterpart** — deliberately, since its audit comments are authored `user`),
`/operator*` (13), and `/agent/*` (4: `GET /agent/task`, `PATCH /agent/task/status`,
`POST /agent/task/comment`, `POST /agent/privilege/request`) under `requireAgentToken` — the
in-devpod agent's own surface, where `PATCH /agent/task/status` **forbids `complete`**.
