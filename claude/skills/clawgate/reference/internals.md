# clawgate — code-level traps

Read when: you are **changing clawgate's Go code or its htmx markup**, or a producer is getting an
unexpected status back. Not needed to merely operate it.

## The markdown renderer (0.7.77) — `internal/ui/markdown.go`

Emitted markup goes into a **vault** keyed by a `\x00`-delimited token (`mdVaultSep`,
`markdown.go:88`) and is restored in **ONE** `ReplaceAllStringFunc` pass — the renderer used to
re-parse its own output (two links in a paragraph destroyed each other's attributes, a `_`/`*` in
a URL corrupted the href, emphasis ran inside code spans). ⚠ **Never make the restore per-item:
it is quadratic** — 33.5s vs 0.7s on a 195 KB body (`maxTaskBodyLen` = `200_000`), on the path
that renders **every task's full body on the LIST page** (`internal/ui/notes.go:550`). Guarded by
`internal/ui/markdown_vault_test.go` (`TestRestoreIsSinglePassAtScale`, 10s bound).

🔑 **The rule that matters: if a client is working around this renderer, fix the RENDERER, not the
client.** A client-side workaround for this class was wrong three times running.

⚠ **Two renderers, two sentinels — don't conflate them.** The Go renderer's sentinel is `\x00`;
the **JS mirror** in `internal/ui/agents_detail.go` uses a different one, `\uE000` (written here escaped on purpose — a literal U+E000 is invisible to `grep`).

✅ **That comment is no longer stale — this note was.** `internal/ui/markdown.go:51-56` now states
correctly that the JS mirror's leak was **fixed in 0.7.79** (`agents_detail.go:843`). Read the
comment, not this paragraph. Residual known bug: a backtick inside a URL renders a truncated
autolink plus literal text.

## 🔑 NAME-COLLISION TRAP — there are TWO `taskTitle`s
`internal/api` has its OWN `taskTitle`, and it is **NOT** `ui.TaskTitle`.

| | `internal/api/push_task.go:359` `taskTitle` | `internal/ui/notes.go:767` `ui.TaskTitle` |
|---|---|---|
| purpose | the **Web-Push notification title** | the **display label** |
| derives from | first non-empty line of the **body**, markdown-**flattened** through `notificationText()`, truncated | `title` → `directory`, **raw** |
| fallbacks | `directory`, then `"New task"` | `directory` |

Body-first vs title-first, flattened vs raw, push vs UI — they answer different questions.
**Do not "consolidate" them** (the code says so at `push_task.go:355-358`).

## Task tags — the full grammar (0.7.75, migration 0018)

`internal/notes/tags.go`. Storage is `notes.tags TEXT[]` + GIN index.

- Input is **lowercased** (never rejected for case); whitespace runs → `-`.
- Charset `a-z0-9._/-`; **at most one `:`** for `namespace:value` (empty namespace *or* value =
  invalid).
- **≤64 runes per tag, ≤20 tags per task**; deduped + sorted; blanks dropped.

Two classes: **free-form descriptive**, and a **closed reserved routing allowlist** —

| namespace | behaviour |
|---|---|
| `runbook:` | **hard**-validated against the runbook store; unknown → **400**. ⚠ skipped entirely in in-memory mode, so this only holds in prod |
| `initiative:` | **soft** — deliberately not resolved (the store is cross-cluster); the initiatives board joins on it |
| `gate:` | soft to validate, but **blocks dispatch** — `agents.go` refuses with **409 "task is gated"**. The disabled UI button is only a hint; the endpoint is the enforcement |
| `auto:dispatch` | **plumbing only, shipped OFF** behind `CLAWGATE_TAG_AUTODISPATCH`; even when ON it just logs — it does not dispatch |

Machine surface: `tags` on POST/PATCH; **`addTags` / `removeTags` merge ops** on
`PATCH /api/tasks/{id}` (so concurrent producers don't lose each other's tags via
read-modify-write; `tags` with either merge key = **400 "ambiguous tag edit"**);
`GET /api/tasks?tag=a&tag=b` (AND; an invalid *filter* value is NOT an error, it just matches
nothing → `200 []`); `GET /api/tags` (vocabulary + counts).

### 🔴 The `400` on an invalid tag is a load-bearing WIRE CONTRACT, not an incidental status
Producers key their fail-open retry on it (repo-cos retries once with `tags` stripped **only** on
400) because a failed POST means "re-propose next week" — so a tag rejection arriving as anything
else silently costs an approval for a week. ⚠ **Neither side pins the cross-service contract**:
clawgate pins 400 in `internal/api/tags_test.go`; the producer's dependence on it is untested.
Rationale: `homelab-talos/claudedocs/clawgate-task-tags-spec.md` §6.

⚠ **An invalid tag or unknown `runbook:` is a hard 400, so a client sending a bad tag breaks EVERY
create** — validate client-side against the grammar above.

## `title` vs `directory` on `POST /api/tasks`

✅ **`POST /api/tasks` HAS a real `title` field (0.7.75).** `handleAPITaskCreate` decodes
`{directory, title, body, model, repo, branch, privileges, tags}`. The display label prefers
`title`, falling back to `directory`, so pre-0.7.75 producers keep working — but NEW producers
should send `title` and leave `directory` for an actual directory.

- ⚠ `directory` is **NOT cosmetic**: `inferRepoFromDirectory` (`internal/api/agents.go`) matches
  it against connected repos to infer the dispatch repo, so smuggling a title through it feeds
  junk into repo inference.
- ⚠ The fallback is **display-label only** (`ui.TaskTitle`): the card's `<h3>` **heading** renders
  ONLY when `Title` is non-empty, so a legacy `directory`-titled card still shows no heading.
- **Legacy is honoured, not deprecated** (`mail-actions`/`repo-cos` still smuggle titles through
  `directory`). The dead "no title field" claim came from `notes.go:122` — the HTML **form**
  handler, not the JSON endpoint; verify against `handleAPITaskCreate`.

## Comment authorship is structurally bounded (`POST /api/tasks/{id}/comments`, 0.7.78)
The author is derived from the bounded `X-Clawgate-Source` allowlist
(`{extension, api, drafter, repo-cos, claude-code}`, unknown → `api`), **NEVER from the body** —
the decoded struct is `{body}` only, so `user` / `operator` are **structurally unreachable**
(pinned by a `reservedCommentAuthors` map + test).

## Naming gotcha: user "task" = code "note"
User-facing "Tasks", but **ALL internals are still "notes"**: Go pkg `internal/notes`, DB tables
`notes` / `note_attachments`, form fields `note_id` / `note_text`, routes serve
`/tasks` → `handleNotesContent`. When grepping, translate.

## 🔴 htmx: attributes resolve UP from the issuer, events bubble UP to you

**An attribute resolves from the element that ISSUES the request**; `we()`→`re()` walks that
element's **ancestors**. So `hx-disabled-elt` on a submit **button** never applies to a
form-initiated submit — the button is a *descendant* of the form, so it is **present but inert**
and a guard asserting "`disabled` appears in the body" passes on a live control. Put
it on the `<form>`: `hx-disabled-elt="find button[type='submit']"` resolves relative to the issuer,
so a lazy child combobox is unaffected. **Conversely `hx-request` IS inherited** — `bn()`
recurses to `parentElement` and, unlike `re()`, ignores `hx-disinherit` — so a timeout set on a
container silently caps **every** descendant request.

**htmx dispatches its events with `bubbles: true`**, and `hx-on::*` handlers are *element*
listeners with `this` bound to that element. So `hx-on::response-error` on a list container fires
for **every descendant's** failed request — a card-level PATCH returning 500 destroyed the entire
list, reporting a failure that never happened. Guard with `event.target === this`. ⚠ `htmx:timeout` is dispatched by `p.ontimeout` and is **neither**
`responseError` **nor** `sendError`, so a timeout is silent unless listened for separately.

## `g.If` gotcha (gomponents)
Its node arg is evaluated **EAGERLY** — a node that dereferences a maybe-nil field (e.g.
`detailSuggestion(v)` on `v.Suggestion`) must be nil-safe or it panics → 500.

## Migrations
`internal/db/migrations/NNNN_*.sql`, applied on startup (advisory-locked). **Append-only — add the
next number, never edit an existing one.**

| # | what |
|---|---|
| 0003 | task status + `note_comments` |
| 0004 / 0005 | privilege profiles |
| 0006 | `runbooks` + `runbook_runs` audit |
| 0007 | perf indexes |
| 0009 / 0010 | chat sessions + `read_at` |
| 0011 | **drops** `decision_labels` |
| 0012 | suggestions |
| 0013 | persisted per-project auto-approve |
| 0014 | per-Task dispatch config |
| 0015 | ⚠ **no new table** — `0015_chat_message_parts.sql` only `ALTER`s `chat_messages`, adding `kind`/`tool_id`/`tool_name`/`tool_ok` for the STRUCTURED transcript (assistant turn = ordered parts; legacy rows = kind `text`). There is **no `chat_message_parts` table**; the filename is not a schema object |
| 0017 | source provenance |
| 0018 | `notes.tags TEXT[]` (GIN) + `notes.title TEXT` |
