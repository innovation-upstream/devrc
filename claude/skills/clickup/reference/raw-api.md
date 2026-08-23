# Dropping below the CLI to the raw ClickUp REST API

Learned building standup-triage over a shared team board. Read this **before**
hand-rolling requests against `https://api.clickup.com/api/v2`.

Auth header is the personal token with **no `Bearer` prefix**:
`Authorization: pk_...`

## A saved *view* is not a list

A URL like `…/v/db/abc12-13711` carries a **view id**, not a list id. Passing it to
`list <id>` or `/list/<id>/task` errors with `Could not parse list id`.

Resolve it: `GET /view/{view_id}` → `.view.parent.id` is the underlying list.
(The `TRIAGE` dashboard's parent was list `900000000001`.)

## Dashboard-type views cannot be queried for tasks

`GET /view/{id}/task` on a `type:"dashboard"` view returns
`PAGE_047 "Must be a task view"`.

Only **list/board** views (`required_views.list`, e.g. `6-900000000001-1`) support
`/view/{id}/task`. To reproduce a dashboard's task set, read its `.view.filters` and
apply them yourself against `/list/{id}/task`.

## 🔴 Both task endpoints paginate — a single-page read silently undercounts

- `/view/{id}/task?page=N` returns **≤30** per page
- `/list/{id}/task?page=N` returns **≤100** per page

Loop until `last_page:true` or a short page. Reading only page 0 made a **67-ticket
queue look like "30"** for an entire session — no error, just a wrong number that
looks like an answer.

## A `/v/db/` view's filter is the authoritative spec of a human workflow

`.view.filters.fields` holds the exact `status` / `assignee` / `creator` / `dueDate` /
`priority` predicates, with `filter_groups` for AND/OR nesting.

If you are automating "the tickets someone triages daily", read that filter rather
than guessing at it — it is what the human actually looks at.

## Field shapes that bite

- `priority.priority` is the **string** (`urgent|high|normal|low`), not the 1–4 int
- `assignees[].username` for owners
- `due_date` is an epoch-**ms string**, or `null`
