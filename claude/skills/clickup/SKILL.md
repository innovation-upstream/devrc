---
name: clickup
description: Interact with ClickUp tasks and documents - get task details, view comments, create and manage tasks, create and edit docs. Use when working with ClickUp task/doc URLs or IDs.
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
- **`claim <task>`** links the current session to a task via the Session ID custom
  field, so work is resumable later. Non-obvious and easy to forget.
- **`--account <name>`** targets a non-default identity on any command
  (`node query.mjs my-tasks --account alex`).
- **Bulk**: most task commands accept comma-separated IDs —
  `node query.mjs status id1,id2 "complete"`.
- **Internal-API commands** (`inbox-*`, `doc-comments`) need a JWT in the account, not
  just a token — see setup below.

## Going deeper

Paths below are relative to this skill directory. Like every other skill, it is
**deployed by home-manager from `~/workspace/devrc/claude/skills/clickup/`**: edit
THERE, then `home-manager switch --flake ~/workspace/devrc --impure` (or
`scripts/ship.sh` for both hosts). What lands at `~/.claude/skills/clickup/` is a
tree of read-only `/nix/store` symlinks — editing it directly is impossible, and a
`git pull` alone changes nothing until you switch.

- **All mutable state lives in `$XDG_STATE_HOME/clickup`** (fallback
  `~/.local/state/clickup`) — credentials included. A write next to the code is
  `EROFS`. Setup, `accounts.json`, multi-account and the JWT fields →
  `reference/setup.md`.
- **`node_modules` is BUILT by nix**, not installed: `nix/pkgs/clickup-node-modules.nix`
  materialises it from `package-lock.json` and links it in at the skill root. To
  change a dependency, edit `package.json` + `package-lock.json`, set `npmDepsHash`
  to `lib.fakeHash`, build, copy the `got:` hash back — never guess it.
- **Hand-rolling raw `api.clickup.com` requests** → `reference/raw-api.md` — read it
  first: a view id is not a list id, dashboard views can't be queried for tasks, and
  both task endpoints paginate (a single-page read made a 67-ticket queue look like 30).

## Tests

The hermetic gates are `node:test` suites, run by devrc's node gate
(`bash scripts/run-node-tests.sh .` from the devrc checkout, and
`nix build .#checks.x86_64-linux.nodetests` in CI). Standalone still works:

```bash
node test/help-coverage.test.mjs      # hermetic; pins showUsage() completeness
node test/state-paths.test.mjs        # hermetic; pins state OUT of the skill dir
node test/js-source.test.mjs          # hermetic; controls for the source scanner
node test/smoke-test.mjs --readonly   # live API, needs credentials — NOT in any gate
```
