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

**SHIPPED AND LIVE in clawgate `0.8.45`.** All three asks merged; the panel was verified against the live pod.

- PR `ZacxDev/homelab-infra#852` → squash **`8438cdb34`**. Deploy pin **`7aee536fc`**.
- Image `sha256:6de86031e14764133bf9c43c6fe331bf54355d98d4772ef2d64a81ac5266da48`.
  🔴 **Pushed digest == RUNNING POD digest** — verified, not assumed. `clawgatectl health` → `0.8.45`.
- Markers read from the **extracted binary** with positive AND negative controls before the pin moved:
  present — `What needs my attention?`, `blocked right now`, `Tapping one fills the box`,
  `Could not search threads`, `data-chief-threads-state`; absent — `enumerate the tmux windows`
  (the deleted capability sentence), `older threads` (the deleted truncation footer).
  ⚠ The instrument needed fixing first: `grep -c` on the 66 MB binary found **nothing at all**, and
  only the fact that *every* positive AND *every* negative control read "absent" exposed it. Routed
  through `strings`; control `clawgate` → 6,700 hits.
- **Verified live in the operator's real Brave**, background tab, no screen taken (`open` + `wake`,
  never `activate`): `#chief-panel-actions` present, panel body 1,359 chars, `#chat-log` +
  `#chat-input` present, `missingState: 0`, and the **thread list rendering two real threads with
  relative timestamps** — the feature that did not exist before this release.
  ⚠ `data-chief-intro` and `data-chief-quick-action` read **0**, which is CORRECT: the intro renders
  only on an empty thread and the active thread has 2 bubbles.
- Ladder: round 0 (3 deletions) → r1 → r2 → r3 → r4 → r5 **cancelled by the operator** ("enough
  audits, merge now and deploy"). Shipped-behaviour per fix round: **63 → 46 → 34 → 0**.
- CI on the merged head: **4/4 green including `clawgate-e2e` at 268 tests**.

🔴 **NOT verified, and these are the gap:** the quick-action prefill, the new-thread button and the
close-and-reopen thread persistence were **never clicked against the live pod**. All three render only
on an **empty thread**, so proving them writes a real `chat_sessions` row in the live database — not
done unasked. The four e2e cases covering them passed in CI on the merged source that built the
deployed image (digest chain + marker check tie the two), but no live click was performed.

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

### ✅ RESOLVED — Does `hx-preserve` survive an ancestor `innerHTML` swap in the vendored htmx, and where does a `fixed` child of the panel actually land?
as-of: 2026-09-18
**Both halves ANSWERED the same day the block was written. Supersedes the block of the same
name above — do not re-derive it.**
- **`hx-preserve` works, and was read out of the vendored htmx 2.0.4 rather than out of docs:**
  the sweep is `fragment.querySelectorAll("[hx-preserve], [data-hx-preserve]")` →
  `document.getElementById(id)`, on the main swap path for every swap style including
  `innerHTML`. ⚠ **But the modern-Chrome pantry path (`Element.moveBefore`) uses
  `querySelector("#"+id)` — a CSS SELECTOR — so a preserved id must be a valid CSS
  identifier.** A raw session id is not guaranteed to be; that is why the pagination fix
  (`ZacxDev/homelab-infra#851`) uses a letter-leading literal prefix plus `replyScopeSlug`
  rather than `sessionChatBodyID`'s raw-id shape.
- **The containing-block hazard is CONFIRMED, and a browser found a second defect reasoning
  would have missed: a 12px SLIVER.** `translate-x-full` moves the shut list by 100% of its
  OWN width to its containing block's right edge — and `#chief-panel-body` carries `p-3`, so
  that edge is 12px INSIDE the panel. Measured at 1280×720: shut list `1268→1703` against a
  panel ending at `1280`, leaving a visible strip of its background and ring down the right of
  the conversation. **The panel's own `overflow-hidden` cannot fix it — the sliver is inside
  it**; the clip has to be on the body root (`relative overflow-hidden`).
- Geometry settled: `absolute inset-0` sub-view of the panel's own column. Desktop panel
  `820→1280` (w 460) with the list `833→1268`, inside it. At 390×844 the panel is the whole
  screen (`max-w-full` caps the stored 460px inline width), `document.scrollWidth` = 390.
- ⚠ **Two specs failed against a CORRECT layout by sampling mid-transition** (`right=1703`,
  and `517` on a 390px phone against a settled `343`). Any assertion on this panel's geometry
  must wait for the slide to settle.

### ✅ RESOLVED — How many `chat_messages` rows are there, and does thread search need an index?
as-of: 2026-09-18
**MEASURED against the live clawgate Postgres (`clawgate-postgres-88947b8b7-7gm8n`, PG 16.15),
2026-09-18. Supersedes the block of the same name above.**
- `chat_messages`: **34 rows**, relation size **248 kB**. By kind: `text` 12, `tool_result` 11,
  `tool_call` 11 — and **no `''`-kind rows**, so filtering on `kind='text'` misses nothing.
- `chat_sessions`: 5 total; the chief (agent 71) holds 2.
- `pg_trgm` is **available (1.6) but NOT installed**.
- **Verdict: plain `ILIKE`, no index, no extension** — a seq scan of 248 kB is sub-millisecond.
- 🔴 **The snippet must be cut IN SQL, and the reason is a measurement: the longest single
  `text` body is 174,163 bytes.** Returning whole bodies to pick a snippet client-side is 174 KB
  over the wire for one result.

## Next steps (ranked)

1. **Click the three empty-thread paths against the live pod** — open the chief panel, tap New
   thread, confirm the intro + three quick actions render, tap one and confirm it PREFILLS
   `#chat-input` without sending, then close and reopen the panel and confirm the picked thread
   survives. ⚠ This writes a real `chat_sessions` row; `handleAgentSessionCreate` reuses an empty
   latest session, so the cost is bounded to one row. Repo `ZacxDev/homelab-infra`, no code change.
   forcing: user — the operator asked for these three behaviours by name on 2026-09-18, and only the
   thread list has been seen working on the live pod.
2. **Add the chief panel to the ux-audit surfaces walk.** `containers/clawgate/e2e/ux-audit/clawgate-surfaces.audit.ts`
   contains **zero** occurrences of `chief` or `tmux` (measured), so `tekton/ux-audit-clawgate` has
   never run axe over the panel or its thread list — and its green was once cited as evidence it had.
   **IN FLIGHT** on `feat/ux-audit-chief-panel`. Closing condition the operator named: the view
   appears in a run's `findings.md`.
   forcing: user — explicitly selected when the ceiling question was answered.
3. **Decide whether `@axe-core/playwright` belongs in the GATING e2e tier.** It exists only under
   `e2e/ux-audit/`, which by design "can never red `make e2e`", and is absent from `package.json`.
   ⚠ The `axe` hits in `e2e/tests/*.spec.ts` are the word *axes*, plural of axis — a substring match
   that read as a present capability and sent one brief chasing a helper that does not exist.
   forcing: none
4. **`internal/ui/notifications_test.go:32`** still asserts `hx-boost="false"` absent from the WHOLE
   document on a different render — the same page-wide-claim-about-two-anchors trap that was narrowed
   in `agents_detail_test.go`, un-narrowed and out of that PR's range.
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

### Added 2026-09-18 (later the same day) — three corrections to this doc's own instructions

- 🔴 **THIS DOC TOLD A FUTURE SESSION TO DO SOMETHING INERT, AND THE CORRECTION IS THE POINT
  OF THIS ENTRY.** The recorded mechanism for remembering the picked thread — *"have
  `chiefPanelScript` apply it to the body's `hx-get`"* — **does not work.** Measured in the
  vendored htmx 2.0.4 bundle: `wt(t,n,e)` reads `te(t,"hx-"+r)` and hands it to `de(...)`, so
  **the path is captured in a closure at PROCESS time**; `#chief-panel-body` is swapped
  `innerHTML` and the element is therefore never re-processed. Rewriting the attribute alone
  changes nothing on the wire.
  🔴 **This package had ALREADY solved and documented it** — `internal/ui/components.go:3858`,
  PR `ZacxDev/homelab-infra#727` on `#tasks-list`, where the attribute read
  `/ui/tasks?limit=100` while the wire carried a bare `/ui/tasks`. The working idiom is the
  attribute rewrite **plus** an `htmx:configRequest` listener scoped by element id, and **the
  test must assert off the WIRE, never off the attribute** — asserting the attribute is what
  makes this bug invisible.
- 🔴 **THERE IS NO axe HELPER IN THE GATING e2e TIER — this doc's verification plan named one
  that does not exist.** `@axe-core/playwright` lives only under `e2e/ux-audit/`, which runs
  from `playwright.ux-audit.config.ts` and by design "can never red `make e2e`"; it is not in
  `package.json` either. **The `axe` hits in `e2e/tests/*.spec.ts` are the word *axes*, the
  plural of *axis*** — a grep that matched a substring of an unrelated word and read as a
  present capability. Adding it to the correctness tier is a new dependency AND a new pattern,
  so it was left as an operator decision; a11y is pinned structurally in Go instead
  (`TestTheThreadListCheckboxKeepsItsAccessibleShape`, guarding both previously-paid axe
  findings). ⚠ The existing `internal/ui/a11y_test.go` scan is real and DID fire — it caught
  four `text-slate-500` contrast violations in the first draft.
- **The panel's title bar cannot host the launchers as plain shell markup.** `ChiefPanel()` is
  rendered by the shell on every tab and the shell deliberately does NOT resolve the chief —
  that absent lookup is what makes the body lazy. Both launchers therefore arrive by
  `hx-swap-oob` (18 existing uses in `components.go`).
- ⚠ **A duplicate-id test that splices a whole response into the body reports a FALSE
  duplicate.** htmx *extracts* `hx-swap-oob` elements before swapping the remainder, so no
  settled document ever holds both copies. A correct counter must perform the oob swap the way
  htmx does, and carry a positive control naming the ids the walk must have seen.
- **`operatorFAB` navigates** — it is a boosted `<a href="/operator">`, so it never mounts a
  second chat in the shell. That closes the duplicate-id question this doc left open as "an
  argument, not a guard".
- 🔴 **`core.hooksPath` measured 2026-09-18: repo-locally set to
  `/home/zach/workspace/homelab-talos/.githooks`, global unset — a THIRD spelling**, distinct
  from both values devrc's `CLAUDE.md` records (`githooks/` and `.git/hooks`). The pre-push
  gate RAN, PASSED all five legs, and **did NOT rewrite the branch** (contrast task #322).
  This does not contradict `CLAUDE.md` — it confirms its instruction to re-measure rather than
  carry the value in prose.
- ⚠ **"Red at trunk" is a COMPILE failure for every new-feature test here**, because each names
  symbols that do not exist on `trunk`. That is honest but uninformative, so the real evidence
  is a mutation matrix (28 mutants, 28 killed). Two of those are worth carrying:
  **(a)** one mutant SURVIVED because the guard did `strings.Contains(script,
  "htmx:configRequest")` over the WHOLE rendered script, and deleting the listener left the
  phrase behind **in the comment explaining why it exists** — green with the mechanism gone.
  Assertions on rendered script must run against comment-STRIPPED code, with a two-way control
  on the stripper. **(b)** one mutant died for the WRONG reason: dropping the `agent_id` scope
  produced a pgx encode error rather than a scoping failure, so it had to be rebuilt keeping
  `$1` bound and typed before the scoping assertion could kill it.
- ⚠ **One test does NOT guard what its name suggests:** *"a DELETED remembered thread heals to
  the latest"* PASSES under the mutant that removes the `configRequest` listener, because
  falling back to latest is also exactly what a BROKEN memory does. It guards the fallback,
  not the mechanism.

### Added 2026-09-19 — shipped; what the five-round ladder actually bought

- 🔴 **Three of the four defects this arc fixed were NOT in the operator's feedback**, and each was
  found by a different instrument than the one being audited:
  **(a)** pressing Send **deleted the chat box** — a verb-less `#chat-form` inheriting `hx-boost` from
  `<body>` and swapping a whole-page response into `#chief-panel-body`'s `hx-target="this"`. Found by
  **CI's browser tier** after three rounds of Go-level probing (hostile SQL through psql, the auth tier
  traced from route registration, duplicate ids counted after an OOB swap) had missed it.
  **(b)** a failed thread read rendered as **"No threads yet"** on the panel's FIRST PAINT — the round
  that fixed the *search* route wrote a comment calling that sentence "THE WORST OF THE THREE" and left
  it on the path its own comment called "the FREQUENT path".
  **(c)** a **truncated** read (rows AND an error) rendered as the complete answer.
- 🔴 **(c)'s real cause is the generalisable lesson: `fakeAgents.ListSessions` could only express
  `(nil, err)`.** So two rounds of guards — all of them real — ran at the one row count where the
  defect did not manifest. **A guard can only observe states its fixture can construct**; no amount of
  additional guarding finds this until the fixture grows a shape.
- 🔴 **A positive control has to ENTER the branch it certifies.** A control cleared the error and saw
  rows appear — proving the fake's `return out, nil` arm works, and saying nothing about
  `return out, err`, the arm the whole axis depended on. Neutering that arm left the package `ok`.
  New category: not a guard that cannot fail, but a guard whose *control* tests the wrong path.
- **The boost message was wrong TWICE in consecutive rounds** — one round fixed the verb arm and broke
  the no-verb arm (`"so htmx WILL boost it"` is false for a non-local link, a bare `<a>`, and
  `href="#…"`). Resolved by implementing htmx 2.0.4's real rule with a third outcome that says the
  verdict depends on the deployment hostname rather than asserting what htmx does.
- **The strongest guard in the arc pins the vendored bundle:** exactly-one-occurrence assertions on
  `version:"2.0.4"` and three source fragments, each labelled with the predicate arm it backs, a
  remediation message, **and a negative control** — because every other assertion is a presence check
  and a presence check that matches nothing is indistinguishable from a broken one. Watched to fail
  four ways including a simulated upgrade applied to the bundle itself, restored and hash-verified.
- **A mutant reported SURVIVED when it had not compiled.** The sweep counted `--- FAIL` lines only.
  Build detection added. One sub-expression (`!ok`, redundant with `href == ""`) is structurally
  unmutatable and is recorded as **unverified by mutation** rather than folded into a clean sweep.
- ⚠ **An intermittency that inverts the usual intuition:** the Send defect's boosted GET fired in
  **100%** of probe runs while the test failed only 3 of 7 locally. It is a race between the response
  landing and Playwright's first probe — `send()` clears the input synchronously, the fetch is async —
  so **a faster server makes the test fail MORE and a loaded one makes it pass.** The guard was
  deliberately moved onto the request log (deterministic) rather than the symptom.
- **Two operator decisions worth not re-litigating:** the quick actions **prefill and never send**
  (a stray tap would otherwise spend an orchestrator turn with no undo), and the intro renders **only
  on an empty thread**. A later round then found the prefill was *destroying a half-typed draft*,
  which falsified the very property prefill was chosen for; it now appends below the draft.
- ⚠ **`clawgatectl` prints `note: server 0.8.45, clawgatectl built for 0.8.44` until a
  `home-manager switch`** — it is built by nix from a LOCAL checkout of this repo, so the deploy pin
  and the client binary move independently. Not a bug; the documented stale-local-build case.

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
