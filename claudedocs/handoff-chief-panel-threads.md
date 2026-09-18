# Handoff: chief-panel-threads — 2026-09-18

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/homelab-talos
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Make the clawgate **chief slide-out** usable as a conversation surface rather than a single
endless thread: an intro with quick actions on an empty thread, a new-thread button, and a
searchable list of previous threads that opens them **in the panel**. Operator feedback,
verbatim, 2026-09-18.

- **closing-condition:** `check` — the chief-panel e2e spec passes the four named cases
  (quick-action prefills without sending · new thread leaves the URL on `/tmux` · a thread
  opens from search · **a picked thread survives closing and reopening the panel**) against the
  DEPLOYED clawgate version, and that version's pushed digest equals the running pod digest:
  ```bash
  git -C /home/zach/workspace/homelab-talos log --oneline -1 origin/trunk -- containers/clawgate/e2e/tests/chief-panel.spec.ts
  # then: run the spec against the deployed version, and compare pushed digest vs running pod digest
  ```
  🔴 **FROZEN AT ROUND 1.** These three items are the arc. A round-2 browser judgement on the
  result is a NEW arc, not another round of this one — the sibling
  `claudedocs/handoff-tmux-webapp.md` ran 13+ ranks past its round-1 objective and says so.

## State now

**Nothing merged, nothing deployed.** This session did recon, settled four design questions with
the operator, and dispatched one agent. Live clawgate is `0.8.44`, which does not contain any of
this.

- Repo: `ZacxDev/homelab-infra` (checked out at `/home/zach/workspace/homelab-talos`; the
  directory name differs from the GitHub repo name, which is correct). Default branch `trunk`.
- IN FLIGHT: branch `feat/chief-intro-and-threads`, worktree
  `/home/zach/workspace/homelab-talos-chief-threads`, cut from `origin/trunk`. **No PR yet**, and
  as of this writing **not yet pushed** — check `git ls-remote --heads origin 'feat/chief*'`.
- Claim: `chief-panel-intro-and-threads` (rc 0; release it when done or abandoned).
- A sibling agent is live in the same repo on `fix/transcript-pagination-survives-refresh`
  (tmux-webapp rank 72), editing `internal/ui/session_view.go`, `session_archive.go` and
  `e2e/tests/session-*.spec.ts` / `tmux-page.spec.ts`. This arc was told not to touch those.
  🔴 **Test-merge current `trunk` before merging either** — the base moved during the session
  (`5f410f8ed`), and disjoint files are not safety.
- **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exited **6**: 595/602/603
  all linked to the session, every one `role=read`, none WORKED. A read is not doing a task's work,
  so no field was recorded. **This arc has no card yet** — opening one is ranked below.

## Open investigations — live diagnosis state

### Does `hx-preserve` survive an ancestor `innerHTML` swap in the vendored htmx, and where does a `fixed` child of the panel actually land?
- as-of: 2026-09-18
- **Symptom + exact repro:** not a failure yet — two mechanics the fix depends on, neither measured.
  (a) The recommended fix for the thread-selection revert leans on `hx-preserve`. (b) The thread
  list is to be rendered inside `ChiefPanel`.
- **Observed (with values):** `ChiefPanel` (`internal/ui/chief_panel.go:121`) carries
  `translate-x-full` and `transition-transform` in its class list, and `inert` when shut.
  `sessionDrawer` (`internal/ui/agents_detail.go:494`) renders `fixed inset-y-0 right-0 z-40
  w-80 max-w-[85vw]` with a `z-30` backdrop. `tmuxPrettyPanel` already uses `hx-preserve`
  elsewhere in this codebase, so there is precedent — but not at this nesting.
- **Ruled out:** "the drawer will overlay the viewport as it does on the agent-detail page" —
  a transform on an ancestor creates a containing block for `position: fixed` descendants, so
  inside the panel it resolves against the PANEL, and a shut panel takes it off-screen and
  `inert` with it. `via: code` — read from the class lists above, **not** observed in a browser.
- **Leading hypothesis:** the drawer must become a sub-view of the panel's own column rather
  than a second fixed overlay; and `hx-preserve` probably works but is not proven here.
- **Next probe:** open `https://clawgate.zacx.dev/tmux` in a real browser at a phone width and at
  desktop, open the chief panel, and read the computed position of the drawer element — then
  break `hx-preserve` on purpose and confirm the preserved node is the one that survives.

### How many `chat_messages` rows are there, and does thread search need an index?
- as-of: 2026-09-18
- **Symptom + exact repro:** search was specified server-side over titles AND message bodies, and
  the sizing decision was deferred to measurement rather than guessed.
- **Observed (with values):** `chat_messages(id, agent_id, session_id, role, content, kind,
  tool_id, tool_name, tool_ok, created_at)` from `internal/db/migrations/0001_init.sql:84` +
  `0009_chat_sessions.sql` + `0015_chat_message_parts.sql`. Indexes: `idx_chat_messages_agent
  (agent_id, created_at)` and `idx_chat_messages_session (session_id, created_at)`. **No
  full-text or trigram index exists.** `ListSessions` (`internal/agents/pgstore.go:299`) is
  `SELECT … WHERE agent_id=$1 ORDER BY updated_at DESC, id DESC` with **no LIMIT**.
- **Ruled out:** "recap conversations will flood the thread list" — `recapSessionKey`
  (`internal/api/chief_agent.go:139`) mints a gateway key that **owns no session row**
  (`internal/api/server.go:401`), so they cannot appear in `ListSessions`. `via: code`.
- **Leading hypothesis:** plain `ILIKE` is adequate at current volume; `pg_trgm` is an extension
  whose `CREATE EXTENSION` can fail on a managed Postgres and should not be added on spec.
- **Next probe:** `SELECT count(*), count(*) FILTER (WHERE kind='text') FROM chat_messages;`
  against the live database, plus `SELECT name FROM pg_available_extensions WHERE name='pg_trgm';`
  before any index is even considered.

## Next steps (ranked)

1. **Land `feat/chief-intro-and-threads`** — review the agent's PR against `trunk` in
   `ZacxDev/homelab-infra`, test-merge current `trunk` first, then merge.
   Files: `containers/clawgate/internal/ui/chief_panel.go`, `internal/ui/agents_detail.go`,
   `internal/api/chief_panel_ui.go`, `internal/api/agents.go`, `e2e/tests/chief-panel.spec.ts`.
   forcing: user — the operator asked for these three changes by name on 2026-09-18.
2. **Cut a clawgate release and verify the four e2e cases against the DEPLOYED version**, not
   against a local tree. Read the live pin before numbering — the operator ships concurrently and
   a mutable-tag clobber has happened before.
   forcing: user — the feature is not delivered until the operator can use it on the real page.
3. **Rank 74 of the sibling arc — the STATIC guard for the swap class — now has a fifth
   instance arguing for it, and this one was caught by recon rather than by any test.** A guard
   asserting the target→descendant RELATIONSHIP would have caught all five at once; a per-site
   ledger would have marked `#chief-panel-body` done after the typed-input fix and missed the
   selected thread entirely. Tracked in `claudedocs/handoff-tmux-webapp.md`, not here.
   forcing: regression — the same class has now reached production four times.
4. **Open a clawgate card for this arc.** It has none: `resolve` exited 6 with three `read`-only
   links. Authoring one is its own interviewed flow
   (`claude/skills/clawgate/flows/task-authoring.md`, enforced by a PreToolUse hook) and must not
   be minted as a side effect of a handoff.
   forcing: none
5. **Follow-ups deliberately cut from scope:** an unread dot in the thread list (`ReadAt` is
   already tracked per session and `MarkSessionRead` is already called, so it is nearly free), and
   scrolling to the matched message from a search result.
   forcing: none

## Gotchas / decisions / dead-ends

- 🔴 **TWO OF THE THREE ASKS WERE ALREADY BUILT AND DARK — check before building.**
  `newChatButton` (`internal/ui/agents_detail.go:327`) and `sessionDrawer` (`:494`) exist, and
  `chiefPanelBody` **already renders `sessionDrawer(v)`** with `Sessions` + `ActiveSessionID`
  populated by `handleChiefPanel`. The launchers (`chatHistoryButton` `:310`, `newChatButton`)
  are rendered in the **agent-page header**, not in `agentChatPane`, which is why the panel has
  the list in its DOM with no way to open it. `handleChiefPanel`
  (`internal/api/chief_panel_ui.go:65`) **already honours `?session=`**. So item 3 is mostly
  wiring, not new UI — the only substantial new build is search.
- 🔴 **Both existing controls navigate AWAY from `/tmux`**: the drawer's entries are boosted
  `<a href="/agents/<name>?session=<id>">` and `handleAgentSessionCreate`
  (`internal/api/agents.go:1107`) answers `HX-Redirect`. Used as-is from the panel they throw the
  operator off the grid. Each needs a panel-aware branch; the agent-detail and operator pages
  have tests pinning the existing behaviour, so it must stay.
- 🔴 **INSTANCE FIVE of the periodic-swap class, found before it shipped.**
  `#chief-panel-body`'s `hx-get` is the static `/ui/chief/panel` and it refetches on **every**
  open (no `once`, deliberately — see its comment). A thread picked from the list therefore holds
  until the panel is closed and reopened, then snaps back to "latest" with nothing saying so.
  Decided fix: remember the selection in the panel's existing `cg.chief.v1.*` localStorage keyed
  on the panel's element id (a session id is a stable reference, which satisfies that file's
  constraint 2) and have `chiefPanelScript` apply it to the body's `hx-get`.
- **Four operator decisions, settled 2026-09-18 — these are answers, not proposals:**
  (1) three quick actions, exactly `What needs my attention?` / `Recap the fleet` /
  `What's blocked right now?`, as a Go constant list pinned two-way by a test;
  (2) **prefill + focus, never auto-send** — a stray tap would otherwise spend an orchestrator
  turn with no undo; (3) the intro renders **only on an empty thread**, so every new thread opens
  on it and no long conversation carries it; (4) search covers **titles AND message bodies**,
  server-side.
- **Two calls made without asking, because they are derivable:** search reads `kind='text'` only
  and **excludes `kind='tool_result'`** (those rows are file contents and command output — a
  transcript is ~1.1 MB and mostly tool results, so searching them makes every query match noise
  and would paste raw file bytes on screen); and the search route is **`requireSession`**, never
  the hook token or the agent tier — it returns message content, the exact sensitivity PR
  `ZacxDev/homelab-infra#838` is being held over.
- **A quick action must go through the EXISTING send path** — fill `#chat-input` and let
  `#chat-form`'s WebSocket handler do the rest. `chief_panel.go`'s header states the reason in
  general form: the panel re-mounts the existing agent chat rather than building a second one,
  because a second chat is a second transcript, a second unread ledger and a second place for a
  streaming bug to live.
- ⚠ **`agentChatPane` hardcodes `#chat-log` / `#chat-form` / `#chat-input` / `#chat-send` and
  `sessionDrawer` hardcodes `#session-drawer`.** `ChiefPanel` is rendered in the home shell on
  **every tab** (`internal/ui/components.go:284`) while `AgentDetailPage` and `OperatorPage` are
  separate documents, so there is no collision today — but that is an argument, not a guard, and
  `operatorFAB` in the same shell was not checked.
- ⚠ **`e2e/tests/chief-panel.spec.ts:361` is a bad neighbour to copy**: it marks `#chief-panel`
  (the never-swapped shell) while its comment claims it protects what is half-typed in the swap
  target `#chief-panel-body`.
- `chief_panel.go`'s header states three constraints, each naming a shipped defect: the
  persistence script cannot live in the swap target; storage keys must be a stable reference and
  never a description; bind once and listen on `document`, not `document.body`. `sessionDrawer`'s
  checkbox-hack a11y shape encodes two previous axe findings — do **not** re-add `aria-hidden`
  or `tabindex="-1"` to the checkbox.

## Defects (batched)

- (none recorded yet — the agent's PR has not been reviewed.)

## How to verify

```bash
# 1. the branch and its PR
git -C /home/zach/workspace/homelab-talos ls-remote --heads origin 'feat/chief*'
gh pr list --repo ZacxDev/homelab-infra --state open --json number,title,headRefName

# 2. the four behaviours, against the DEPLOYED version — not a local tree
#    quick action prefills and does NOT send; new thread keeps the URL on /tmux;
#    a thread opens from search; a picked thread SURVIVES closing and reopening the panel.

# 3. the search route is operator-only
#    200 with an operator session; 401/403 with the hook token and with an agent credential.
```
