---
name: clickup
description: "Read and act on ClickUp tickets and docs — threads, assignee, priority, workspace search, subtasks, attachments. Use for: a pasted app.clickup.com/t/ link or a bare ClickUp id, my ClickUp tickets, what ClickUp has assigned to me, comment on / update / close a ticket, mark it in progress, set a due date, a ClickUp doc or page. This is the EXTERNAL ClickUp workspace — the self-hosted approval UI and ITS Tasks are `clawgate`, the durable cross-repo board is `initiatives`, the email action-items queue is `mailbox`, and verifying from session transcripts whether work on a task was actually done is `check-clickup-addressed`."
---

# ClickUp

Task and document interaction via the ClickUp API — read tasks and comments, post
updates, manage assignments/dates/tags, and create or edit docs.

```bash
node query.mjs <command> [options]
```

## Finding a command — ask the CLI, not this file

```bash
node query.mjs              # all 63 commands, grouped by area
node query.mjs <command>    # usage for one command
```

🔴 **This file deliberately does not list the commands.** It used to, and the tables
drifted to 56 of 68 — a whole command group and `batch-create` were invisible here
for months. `showUsage()` in `query.mjs` is the single source of truth, pinned by
`test/help-coverage.test.mjs`, which fails if any dispatchable command is missing
from the help (or any printed command cannot dispatch).

**Adding a command? Add it to `showUsage()`** — devrc's node gate runs the check.
Do not restate the list here.

## Hot path

```bash
# Task by URL or bare ID; --subtasks to include children
node query.mjs get https://app.clickup.com/t/86abc123
node query.mjs get 86abc123 --subtasks

# Status: list valid statuses first, then set (case-insensitive, partial match)
node query.mjs status 86abc123              # lists what this list allows
node query.mjs status 86abc123 "in progress"

# Comments: expand threads inline; post long text from a file
node query.mjs comments 86abc123 --threads
node query.mjs comment 86abc123 --file /tmp/update.md --cleanup

# Docs: read a page as markdown, edit it from a file
node query.mjs page <doc_id> <page_id>
node query.mjs edit-page <doc_id> <page_id> --file /tmp/page.md
```

## What the usage lines can't tell you

- **`--file` over `--content`.** `--content` is for a short sentence; anything longer
  goes in a file, because shell quoting mangles markdown. `--cleanup` deletes the file
  after a *successful* run.
- **`@mentions` use bracket syntax** — `[@alex]`, `[@email]`, or `[@userId]`:
  ```bash
  node query.mjs comment 86abc123 "ping [@alex] please review"
  ```
- **`search` cost model.** Default look-back is `--since 30d`, narrowed server-side.
  `--all-spaces` (includes archived) and `--all-time` have **no server-side filter** —
  they pull full history and are slow. `--me` / `--assignee` narrows server-side and
  stays near-instant at any window.
- **`awaiting` measures ONE thing: the newest comment on a task is not the token
  owner's.** Comment-level `resolved` is not readable through the API and ClickUp has
  no bot identity, so "unresolved" is not computable and *anything* this token posts
  reads as the owner answering. It fans out one request per task — hence `--max`, and
  hence the examined/matched/truncated counts it always prints. It is the cheap TRIAGE
  half: it says WHICH tasks have someone else's comment last, never whether the work got
  done. That verdict is the `check-clickup-addressed` skill, which reads the session
  transcripts — run `awaiting` first, then hand it only the tasks that matter.
- **`claim <task>`** links the current session to a task via the Session ID custom
  field, so work is resumable later. Non-obvious and easy to forget.
- **`--account <name>`** targets a non-default identity on any command
  (`node query.mjs my-tasks --account alex`).
- **Bulk**: most task commands accept comma-separated IDs —
  `node query.mjs status id1,id2 "complete"`.
- **Internal-API commands** (`inbox-*`, `doc-comments`) need a JWT in the account, not
  just a token — see setup below.
- 🔴 **Every task this skill creates is STAMPED as agent-created, automatically.**
  `create`, `create-subtask` and `batch-create` all route through one choke point, which
  appends `<!-- claw:obj v=1 src=<producer>/<run-id> cond=<id> -->` to the description
  and attaches the tag `agent/<producer>`. The tag is the load-bearing half **on this
  platform specifically**: the API token resolves to a HUMAN identity, so an agent-filed
  task is otherwise indistinguishable from a hand-typed one. Pass **`--cond`** to record
  what will close the task — from the enumerated allowlist `gh_pr_merged:<owner>/<repo>#<n>`,
  `alert_cleared:<name>`, `cmd_exit_zero:<id>`, `metric_below:<id>`, `manual:<who>`. It
  works on **`create` and `subtask`** alike, and a batch plan takes the same value as a
  `cond` key per task and per subtask (validated plan-wide *before* any task is created).
  Anything else is REJECTED — **including a bare `manual`**, which names nobody, and
  **including `unstated`**, which is what the code records when you named nothing and is
  not something a caller may claim. Rejection happens at the create seam, not just at the
  CLI. Omitting `--cond` records `cond=unstated` and warns on stderr: an honest, greppable
  marker of ABSENCE, so tasks filed with no closing condition stay countable
  instead of reading as compliant. ⚠️ The tag is
  **best-effort** — ClickUp requires a tag to exist at the SPACE level first, and creating
  one is a workspace mutation this will not do silently, so a missing space tag warns on
  stderr and the task is still created with its marker. Opt out with `CLICKUP_AGENT_STAMP=0`
  when a human is genuinely the author.

## Task hygiene — read it before you create or close one

🔴 **Creating or closing a ClickUp task? `~/.claude/skills/clickup/flows/task-hygiene.md`.**
Pre-verify before creating (7 of 8 inbound tickets dissolve on verification, so
"already done / already exists" and creating NOTHING is a success); one completion
comment with evidence **per acceptance criterion** plus an explicit NOT-verified list;
and **do not mark complete when you derived the criteria yourself** — the API token is a
HUMAN identity here, so an agent closing a ticket is indistinguishable from the human
doing it. Two comments per task, never per turn. ⚠️ **Nothing enforces any of it** —
there is no ClickUp hook; it is a convention, and the flow says so first.

## Going deeper — load ONE only when its trigger fires

- **Credentials, `accounts.json`, multi-account, the JWT fields, first-time setup**
  → `~/.claude/skills/clickup/reference/setup.md`. State lives in
  `$XDG_STATE_HOME/clickup` (fallback `~/.local/state/clickup`), never next to the
  code — a write there is `EROFS`.
- **Hand-rolling raw `api.clickup.com` requests** → `~/.claude/skills/clickup/reference/raw-api.md` — read it
  first: a view id is not a list id, dashboard views can't be queried for tasks, and
  both task endpoints paginate (a single-page read made a 67-ticket queue look like 30).
- **CHANGING this skill** — where to edit so the change deploys, the nix-built
  `node_modules` + `npmDepsHash`, and the test suites →
  `~/.claude/skills/clickup/reference/maintaining.md`.
- **"`check-clickup-addressed` re-derives `awaiting`'s predicate — consolidate them"**
  → `~/.claude/skills/clickup/reference/awaiting-vs-ccua.md`. It cannot; the answer is
  measured, not argued. Read it BEFORE proposing that ccua call `query.mjs awaiting`.
