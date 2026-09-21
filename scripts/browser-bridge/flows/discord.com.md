# discord.com — the web client: URL-shaped nav, a Slate composer, and a hidden DOM that keeps everything

**Load this when:** a result envelope named this file in `site_flows` · you are
about to drive or read `discord.com` (also `ptb.`/`canary.discord.com` — same
client) · a hidden read returned no messages and you are about to call the
channel empty · you need to send or read in a channel · you are about to
report Discord broken from a browser read.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md` (`reference/…` paths
below resolve from that bridge dir).

Measured live 2026-09-20 on the laptop's `work` instance (channel
`/channels/<guild>/<channel>`, `#notes`). Facts below were each reproduced with
the bridge op named beside them; anything not yet reproduced is `⚠`.

---

## 🔴 Navigate by URL, never by sidebar clicks

`open https://discord.com/channels/<guildId>/<channelId>` is the deterministic
channel lane — the URL carries everything, so no channel-list clicks are
needed. The guild rail and channel list are NOT reliable to drive blind: the
sidebar tree renders near the viewport, and unread markers live in
`aria-label`s, not text (`js` saw `[role=treeitem]` = 44 nodes whose labels
read "Unread messages, Civitai Internal" etc.). To find a guild/channel id:
read the current url from `context`, or search from the user's own Discord.

## Reads: hidden vs wake'd

- The tab is usually hidden. A hidden `text` read still answers — but returns
  the SIDEBAR (`Direct Messages`, server groups, boost levels), not the message
  pane: a naive read "shows an empty channel". Do not conclude absence from it.
- The SPA DOM is RETAINED while hidden: `js` queries the real tree without
  waking (`[role=article]` = 45 rendered messages on a quiet channel).
- `wake` (or `--wake` on a read) un-throttles via CDP (`Page.setWebLifecycleState`
  + `Emulation.setFocusEmulationEnabled`, settle 1500 ms) and flips
  `visibilityState` to `visible` WITHOUT moving focus. Never
  `activate` for a read; it steals the operator's screen.
- Messages ARE `[role=article]`; `textContent` carries `Author — M/D/YY, H:MM`
  then the body. Deterministic extraction:
  `js "(function(){return [...document.querySelectorAll('[role=article]')].map(a=>a.textContent.slice(0,120))})()"`.

## DMs: find the person, then read (measured 2026-09-20)

DM channels live under a different URL shape: `/channels/@me/<dmId>` (no
guild id). The deterministic find flow, in a tab this session owns:

1. `open` (owned tab) → `nav https://discord.com/channels/@me` → `wake`.
2. The DM rail is plain anchors: every `a[href*="/channels/@me/"]` carries
   `aria-label="<Name> (direct message)"` (or `"<Names> (group message)"`,
   `<N> Members`). Match the name case-insensitively; the `href` tail IS the
   dmId. List order is recency — the first anchor is the most recent DM.
   (Measured: 30 anchors answered without scrolling; group DMs with the same
   person match too — the `(direct message)`/`(group message)` suffix tells
   them apart.)
3. `nav https://discord.com/channels/@me/<dmId>` → `wake` → extract
   `[role=article]` (last-N for "recent").

🔴 **A freshly nav'd channel lazy-loads its history: measured ~10 s from wake
to hydration on a quiet DM** (0 articles at ~4 s, 11 by ~10 s; three probes
4 s + 3 s + 3 s succeeded on the third). Probe on a retry loop, never conclude
an empty channel from one early read — this is the same trap as the hidden
read above, one layer deeper.

## Composer (Slate)

- `[role=textbox]` is the message editor — a `DIV`, classes
  `markup__… editor__… slateTextArea_…`, `aria-label="Message <channel>"`.
  The surrounding area matches `[class*=channelTextArea]`.
- `eval`/`js` runs in the page's MAIN world and WORKS on discord.com (no CSP
  eval block — measured `js "1+1"` → `2` on a hidden tab). Prefer `js` for
  extraction here.
- ⚠ UNMEASURED: the trusted-click → type → `key Enter` send sequence, and menu
  toggles (threads, emoji picker, context menus). Drive these in a tab this
  session owns (`open` + `nav`), never on the operator's live tab, and NEVER
  send without the operator asking — `Enter` in the composer sends.

## 🔴 Secrets

Discord carries private messages. `agent`-mode sends page content to
OpenRouter/DeepSeek — for DMs and private servers drive DIRECTLY, and never
brief DM content into an agent goal. Public/community reads only, by
exception.
