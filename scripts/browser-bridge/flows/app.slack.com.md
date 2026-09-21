# app.slack.com — the client-v2 app: CSP-blocked eval, a Quill composer, and an escape hatch in `js --wake`

**Load this when:** a result envelope named this file in `site_flows` · you are
about to drive or read `app.slack.com` · a `js`/`eval` came back `null` and you
are about to call the bridge broken · you need to send or read in a channel or
thread · you are about to report Slack broken from a browser read.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md` (`reference/…` paths
below resolve from that bridge dir).

Measured live 2026-09-20 on the laptop's `work` instance
(`/client/<teamId>/<channelId>`, `#<channel>`). The running client is
Slack's NEW app (`html` root carries `data-app="client-v2"`) — the old
`data-qa=virt-list-item` / `message-input` selectors are GONE (counted 0).

---

## 🔴 Plain `eval` is CSP-null here; `js --wake` is the escape hatch

`eval`/`js` runs in the page's MAIN world and `app.slack.com`'s CSP forbids
`new Function`/`eval` — every plain `js` op answers `value: null` with NO error
(measured `js "1+1"` → `null`). That null reads like a broken bridge; it is
CSP. The deterministic alternatives, in order:

1. `text` / `html` reads — they inject in the ISOLATED world, which CSP does
   not gate, and carry the full channel content (authors, timestamps, thread
   counts). `html` is ~389 KB — always cap with `--max-bytes`.
2. `js --wake "<expr>"` — the wake path evaluates via CDP `Runtime.evaluate`,
   which bypasses page CSP (measured `js --wake "1+1"` → `2`).
3. For selector work: `js --wake` with a `querySelectorAll` probe.

## Reads: hidden vs wake'd

- 🔴 **A FRESH hidden tab does NOT hydrate, unlike discord.com.** Measured on an
  owned tab: nav to the channel → wake → four 4 s retries, an inline
  `--wake=6000` read, then a trusted click on a channel anchor — the read
  stayed rail-only (~300 B) throughout. Slack client-v2 gates its message-list
  render on real visibility, which emulation does not satisfy. Consequence:
  do channel READS on a tab that is already hydrated (the operator's live
  Slack tab, read-only) — do not burn minutes retrying a fresh hidden tab,
  and do not conclude breakage from it.
- A hidden read on a HYDRATED tab answers with the workspace RAIL only
  (`Search`, `DMs`, `Activity`, `Files`, `Later`, `Agents & tools`,
  workspace switcher) — not the message pane.
- `wake` (or `--wake`) un-throttles via CDP (`Page.setWebLifecycleState` +
  `Emulation.setFocusEmulationEnabled`, settle 1500 ms) without moving focus,
  and the same read on a hydrated tab then carries dated messages
  (`Tuesday, August 25th … Zach Lowden … <body> … 7 replies … View thread`).

## Threads

- A wake'd `text` read of a channel IS the thread-activity summary: each
  thread-bearing message block reads `<body> → "N replies" → "Last reply <age>
  ago" → "View thread"`. Enumerate those blocks for "recent thread activity"
  without opening anything (measured on the same channel: the recent window
  carried a 7-replies thread, last reply 24 days, and a 3-replies thread,
  last reply 6 days).
- Thread CONTENT is not in the main channel DOM — the drawer is its own flow
  and needs a trusted `click` on `View thread`, which must happen in a tab
  this session owns... which is exactly the tab that will not hydrate (above).
  ⚠ UNMEASURED: the drawer read. The untested escape is `activate` on the
  owned tab (a real focus steal — operator's call, never routine).
- The `Threads` rail item aggregates every thread the user is in across
  channels, sorted by recency — the natural lane for "thread activity"
  beyond one channel. ⚠ UNMEASURED: its read (same drawer/hydration
  constraints).

## Composer (Quill)

- `[role=textbox]` is the message editor — a `DIV`, class `ql-editor
  ql-blank`, `aria-label="Message to <channel>"`, `data-qa="texty_input"`.
- `data-qa="message-input"` is GONE in client-v2 (counted 0) — old playbooks
  and memory of it will not fire.
- ⚠ UNMEASURED: the trusted-click → type → `key Enter` send sequence, and the
  formatting bar / slash-command flows. Drive in a tab this session owns;
  NEVER send without the operator asking.

## Workspace switching

- The deterministic lane is open-by-URL:
  `https://app.slack.com/client/<teamId>/<channelId>` (read the live ids from
  the operator's live tab url). ⚠ UNMEASURED: the workspace-switcher UI flow (the rail's
  workspace button → picker) — measure before driving it on the live tab.

## 🔴 Secrets

Slack carries private and work-internal messages. `agent`-mode sends page
content to OpenRouter/DeepSeek — for DMs and internal channels drive DIRECTLY
and never brief their content into an agent goal.
