# Proposal: flows for Discord and Slack (browser-bridge)

Derived from live recon 2026-09-20 (workbench, instance `work`, extension build
`66b98084daecd880` current). Companion to the flows rename
(`reference/sites/` → `flows/`, field `site_notes` → `site_flows`).

## Measured current state

- Bridge connected and current; messaging ops (`health`/`tabs`/`context`/`ping`)
  all answer.
- **Discord tab is live**: `https://discord.com/channels/<guild>/<channel>`
  (`#notes`, "Zacx's server"); title shape `(3685) Discord | #notes | …` — the
  leading `(N)` is the unread badge.
- **No Slack tab is open** in this instance — `app.slack.com` will need one
  (either Zach opens it, or we `open https://app.slack.com/client` into an owned
  tab once host access works, riding the profile's logged-in session).
- 🔴 **Injection is refused on EVERY host right now.** `text` and `js` on both
  the Discord tab and a civitai tab return
  `Cannot access contents of the page. Extension manifest must request
  permission to access the respective host.`
  The manifest carries `<all_urls>`, and tab-metadata ops work — so this is
  **host-access denial, not a code bug**. Prime suspect: Brave's per-extension
  *Site access* setting ("on click"/"on specific sites"), second suspect the
  documented reload trap (`reference/errors.md`). This failure mode is **not in
  `errors.md` yet** — it lands there once the cause is confirmed, because it is
  exactly the "reads like the site is broken" trap an agent hits on any host.

## Step 0 — restore host access (operator, ~30 s)

1. `brave://extensions` → **Browser Bridge (command channel)** → Details →
   **Site access** → **On all sites**.
2. If still refused: reload ↻ the extension (may prompt to re-confirm the
   `debugger` permission), then `$BB health` → `extension_connected:true`.
3. Hand back; I re-probe `text` on the Discord tab before anything else.

## Shape — no new code: two docs + two registry rows

`flows/discord.com.md` and `flows/app.slack.com.md`, registered in
`flows/_index.json`:

- key `discord.com` — host-suffix matching covers `ptb.`/`canary.discord.com`
  (same client app) for free.
- key `app.slack.com` — exact host; `files.slack.com` etc. stay unrouted on
  purpose (the flows live in the app, not file previews).

The two-way ledger (`tests/test_site_flows.py`) forces each file to land
**with** its key in the same commit — a doc without a key is unreachable, a key
without a doc routes nowhere, and the ledger fails on either.

## What each doc derives — live, measured, nothing from memory

**discord.com**

- Identity: logged-out → `/login` vs logged-in → `/channels/<guild>/<channel>`;
  deterministic nav-by-URL (`open https://discord.com/channels/<g>/<c>`) as the
  channel-jump lane instead of sidebar clicks.
- Virtualization: channel list and member list render only near the viewport —
  "absent from DOM" must be proven with `text --annotated` + scroll/wake before
  it is believed.
- Composer: contenteditable — trusted `click` first, `type`, send = `key Enter`.
- Menus/pickers/threads: toggle semantics — trusted `click` **once**, assert
  `aria-expanded`; a JS `.click()` will not open them.
- CSP: measure whether `js`/`eval` returns `null` (GitHub-style strict CSP) —
  if so the doc pins `text`/`html` as the only reliable reads.
- Hidden-tab throttling: `wake` once per page; reload re-throttles.
- 🔴 Chat content policy: **drive DMs/private servers directly** — agent mode
  sends page content to OpenRouter/DeepSeek; private messaging surfaces are the
  same class as private mail.

**app.slack.com**

- Identity + workspace switcher flow (multi-step); deterministic lane is
  open-by-URL `https://app.slack.com/client/<team>/<channel>`.
- Sidebar is virtualized — same prove-absence discipline as Discord.
- Composer `[role="textbox"]` → `type` → `key Enter`; `/`-commands and the
  formatting bar are toggle flows.
- Threads: drawer flow; replies are NOT in the main channel DOM; the
  "also send to thread/channel" checkbox is a read that lies.
- Unread badges in the title; yesterday-divider lazy loading.
- 🔴 Same agent-mode exclusion for DMs.

## Derivation protocol

Each claim is reproduced on the live tab with the bridge op that proves it
(`context` → `text --annotated` → trusted `click` probe → `wake`), and the doc
records the op sequence plus the observed value. A claim with no live
observation gets an `⚠ UNMEASURED` tag, never prose confidence. Seed docs stay
lean (~120–180 lines): what a naive driver would get wrong, nothing more; they
grow by use.

## Validation / acceptance

1. Every documented flow is driven **once as written** against the live tab —
   the doc is a runnable procedure, not lore.
2. Ledger green: registry keys ↔ `*.md` sets identical
   (`test_site_flows.py`).
3. Byte gates green (`test_skill_size.py`), node tier green.
4. The confirmed host-access failure mode is added to `reference/errors.md`
   with its recovery.

## Sequencing with the pending flows rename

The running bridge still executes the old deployed server.py, so annotations
will not fire for the new keys until commit → `scripts/ship.sh` (switch +
`X-Restart-Triggers` restart). The docs themselves are readable from the repo
path immediately. After ship, live-verify the routing:
`$BB --tab <discord-tab> context` → envelope carries
`site_flows: flows/discord.com.md`.

## Open questions

1. Discord: which guilds/channels matter (the live one is a personal server)?
2. Slack: which workspace(s) — and is the profile logged in?
3. Agent-mode stance on chat content: hard exclusion for DMs (proposed), or
   per-task judgement?
