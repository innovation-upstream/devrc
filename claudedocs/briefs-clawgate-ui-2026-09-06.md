# Briefs: clawgate UI feedback round — 2026-09-06

Operator feedback, processed into three sequential PRs. **All code is in
`homelab-talos` (remote `ZacxDev/homelab-infra`) under `containers/clawgate/`.**
This doc lives in `devrc` because the tmux-webapp design record does — see
`claudedocs/handoff-tmux-webapp.md`.

🔴 **SEQUENTIAL OFF `trunk`, NEVER STACKED.** Each PR branches off `trunk` after
the previous one merges. The layer boundaries are real and the closing conditions
differ; a stacked child silently mis-merges when the parent is squashed.

## What the recon changed about the ask

Six of the eleven feedback items were cheaper or more expensive than they looked,
and three collided with decisions recorded as deliberate. Measured 2026-09-06:

| item | finding |
|---|---|
| "move auto-approve to a header button + dropdown" | **Already shipped** in `#667` — `#auto-approve-popover` (`components.go:1366`), trigger `:1285`. Only the loud `⚠ Auto-approve ALL is ON` bar (`:1499`) is still in flow, and rank 20 kept it there deliberately. |
| "session page should render markdown" | **Cheap.** `renderMarkdown` already exists (`markdown.go:222`) and is used by `notes.go` + `agents_detail.go`. `chat.go` renders `g.Text(e.Text)` with `whitespace-pre-wrap`. |
| "tool badges have no details" | **Expensive.** The payload is discarded at PARSE time — `transcript/parse.go:399` builds `Event{Kind, ToolName, At, Sidechain}` and keeps no input. Parser + store + API + UI. |
| "why does it say END of the session" | **Working as designed.** `chat.go:93-108` is a deliberate honesty block: over 5,614 real records, 360 were assistant prose and 78 a human typing, so the view renders ~8%. Two reductions stack — a 256 KiB byte tail (`transcript.go:37`) AND record-type filtering. |
| "why is there no chat input" | **Structural.** A reply control exists (`chatQuestionBlock`) but is gated on an open `AskUserQuestion` (`q.Reply != nil`). Free-form does not exist. |
| "clicking a card's chat button" | The card **already** carries an inline `ChatMount` plus an `open full session →` link (`tmux.go:1388`). |

## Operator decisions taken (2026-09-06)

1. **Fold the ALL-is-ON bar into the header, WITH a loud armed header state** —
   knowingly reverses rank 20. The armed state being legible without opening the
   dropdown is a hard requirement, not a nicety.
2. **Free-form chat input: any session, always.**
3. **Tool detail: full input, expandable on click.**
4. **Raise the global width cap.**
5. Chat button opens `/session/{id}`; inline mount stays as a preview.
6. Prose blocks keep a ~70ch inner cap inside the raised container.

🔴 **Decisions 2 and 3 both widen the same surface**, and rank 34 is the open item
that covers it: `requireSession` is a literal `return next` (`internal/api/auth.go:40-42`),
so the LAN NodePort authenticates nobody. Taken with the blast radius stated.

---

# PR 1 — layout, markdown, and the auto-approve fold (`internal/ui` only)

Cheapest and highest-visibility. No parser, store or API changes.

### (a) Width

`contentWidth()` (`components.go`) is today:

    mx-auto w-full max-w-xl px-4 lg:max-w-5xl xl:max-w-6xl 2xl:max-w-[96rem]

Raise the `2xl` cap. Give long-form text blocks (task body, notes, chat bubbles)
an inner ~70ch cap so paragraphs do not become 3400px lines on the 3440 display.

🔴 **`contentWidth()` is shared by the shell header row and every tab so they
cannot drift, and rank 23 shipped an *iff* guard over all five documents**
(`content_width_routes_test.go`: `TestRouteContentWidthRelationship`,
`TestRouteHeaderMatchesItsOwnContentColumn`). Extend it — do not route around it,
and do not weaken the header/column relationship.

🔴 **A MEASURED SURVIVOR IS RECORDED AT `e2e/tests/responsive.spec.ts:245-247`:**
*dropping the 2xl step entirely leaves the xl cap in force, and 1920 is still
wider than 1280 — that mutant survived a 1280/1920/3440 assertion set.* Pick
assertion points that can see it, and watch it go red.

### (b) tmux cards — min width and adaptive reflow

`tmux.go:838` is `grid grid-cols-1 items-start gap-3 p-3 lg:grid-cols-2` — two
breakpoints, no min width. The min-width ask and the responsive ask are the same
fix: an auto-fit grid with a minimum track, so TUI content gets horizontal space
and the column count follows the viewport instead of a single `lg:` step.

### (c) Markdown in the session chat

Route `ChatUser` / `ChatAssistant` (and decide `ChatThinking`) through the
existing `renderMarkdown`. Mirror `e2e/tests/agent-chat-markdown.spec.ts`, which
already pins the agent-chat equivalent: links render, code spans and blocks are
left untouched.

⚠ `chatEvent`'s header comment is load-bearing: *every kind gets its own shape and
its own label, and no two may share either* — most of all a tool RESULT, which the
parser refuses to render because it would appear in the operator's own voice.
Markdown must not blur those shapes.

### (d) Reword the truncation notice

`chat.go:494` — "this is the END of the session, not all of it". It is TRUE and it
must stay; make it legible (say that only a tail is stored AND that non-prose
records are filtered, with the counts already in `Skipped`/`Malformed`/`TailBytes`).

🔴 **Do not delete it.** That is the shape rank 12 warns about — damage in service
of a bar a healthy system cannot clear.

🔴 **Pin the new wording as a WHOLE NORMALISED STRING.** A word-level guard is
walkable by rewording.

### (e) Auto-approve fold

Remove `globalAutoApproveBanner` from page flow (`components.go:159`, `:1437`,
`:1483`, `:1499`); the per-project list is already in the dropdown. The header
button gains a persistent armed treatment plus count.

🔴 **FLIP THE GUARD, DO NOT DELETE IT.** The test that pins the bar present-in-flow
becomes one that pins the armed header state legible-without-opening. Same
symmetry rank 32 used on `enableTmuxReplyAgent`: deleting it leaves the firehose's
visibility unguarded in both directions.

### (f) Chat affordance on the tmux card

Make the card's chat area a real affordance onto `/session/{id}`; keep the inline
`ChatMount` as a preview. ⚠ Keep `hx-boost="false"` — `/session/{id}` is a
standalone document and a boosted swap splices a whole `<html>` into the panel.

### Tests — PR 1

**Go** (`clawgate-ci`): extend the `content_width_routes_test.go` iff to the new
cap; flip the auto-approve header guards; assert `chat.go` routes through
`renderMarkdown` with code spans/blocks untouched; pin the reworded notice as a
whole normalised string.

**Playwright** (`clawgate-e2e`): `responsive.spec.ts` already runs
`WIDTHS = [390, 1280, 2560, 3440]`. 🔴 **`tmux.spec.ts:1100` only runs
`[390, 1280, 2560]` and MUST gain 3440**, or the suite is structurally blind to the
dimension being changed. New `session-chat.spec.ts` cases for markdown and the
notice; `auto-approve-header.spec.ts` for the armed state at every width.

---

# PR 2 — tool inputs, expandable

### The shape

`transcript/parse.go:399` drops the payload. Carry it: parser → stored tail →
API → `ChatEvent` → a `<details>` on the badge (`chat.go:441`, the `ChatTool` arm).

🔴 **CAP PER RECORD.** `MaxTailBytes` (`transcript/transcript.go:37`, 256 KiB)
bounds the **SUM**, so one 200 KiB file write would evict the rest of that
session's conversation — the feature destroying the thing it renders beside. Full
input up to a per-record cap, with an explicit truncation marker.

🔴 **DISCLOSURE, STATED NOT ASSUMED.** Tool inputs carry file contents, bash
bodies and paths onto a page reachable from an unauthenticated LAN NodePort. That
is a decision, and it belongs in the PR body.

### Tests — PR 2

Parser keeps the input (red at base); the per-record cap at a boundary, with
fixture bounds that **overshoot** rather than sit on the boundary; and the
load-bearing one — **an oversized tool record does not evict conversation
records** from the same session's tail. e2e: the badge expands on click and shows
the input.

---

# PR 3 — free-form chat input

Always-available input on the session page, posting to `POST /ui/term/send-keys`
(`termwrite.go:141`, `TierBrowser`, behind `requireArmedTerminalUI`).

Four requirements, none of which narrow the ask:

1. 🔴 **It states its target.** Precedent already exists and is good — the
   question reply renders *"replies to workbench %480 · presses Enter"* and
   confirms *"Send "gamma" to workbench %480 and press Enter?"*. Silent
   wrong-pane delivery is the failure mode with no signal.
2. 🔴 **Confirm before send**, as the option buttons already do (`hx-confirm`).
3. 🔴 **Idempotency key**, as the option buttons already carry.
4. 🔴 **Rate limit.** A spawn/write route on an unauthenticated LAN NodePort is a
   fork-bomb primitive without one.

⚠ **The `=` prefix hazard does NOT apply here** — measured 2026-09-06. The reply
path targets a pane id (`send-keys -t %480`, agent line 687) and does no name
matching. `=` belongs to `new-window` (agent line 568), the rank-31
start-a-session path. The handoff's rank 33 conflates the two.

### Tests — PR 3

The input names host+pane; the send carries a confirm and an idempotency key; the
rate limit refuses past its bound (watched red without it). e2e: typing and
sending queues a write addressed to the right host and pane.

---

## Standing rails for all three

- 🔴 **Mutation-check every new guard**: watched RED on pre-change code, killed by
  **its own** message, M0 unmutated control green either side.
- 🔴 **Read the PipelineRun's NODE before debugging a red.** Ranks 17/18:
  `clawgate-e2e`/`ux-audit` produce health-check-budget reds unrelated to the diff,
  and the `go` leg reds on `talos-uvh-gtj` (~59x slower fsync).
- 🔴 **Read the leg's own counts, not the check summary.** The hook leg prints
  `plan=/ok=/ran=/floor=`; `clawgate-e2e` prints a test total.
- **Verify a merge by CONTENT on `trunk`, never by ancestry** — a squash is never
  an ancestor.
- **Worktree, not the primary clone.** `homelab-talos` is usually not the session
  cwd, so `isolation: "worktree"` would worktree the WRONG repo; run
  `git -C /home/zach/workspace/homelab-talos worktree add` explicitly.

## Related state

- The reply loop was **broken and is now fixed** — `#735`, squash `0d553fcd`. The
  hook labelled this machine `nixos` (both machines are) while the agent claimed
  `workbench`, so every UI reply expired undelivered. Verified end to end after
  the fix: `delivered s0FeuWTUOkuN0Lu5mCEsfg (pane %480)`.
- 🔴 **The residual from that fix is the shape, not the value:** nothing asserts
  the LEDGER of host-label writers is complete. A fourth producer reproduces the
  defect exactly while every existing guard stays green.
