---
name: browser
description: Drive the user's LIVE, logged-in Brave browser from Claude Code — read the active tab's HTML, run JS in it, list/navigate tabs, and screenshot the visible tab — via the local token-authenticated browser-bridge (loopback rendezvous server + MV3 extension). Use when the user asks you to look at / read / scrape / interact with a page THEY have open, act on an authenticated site they're logged into, check what's on their screen in Brave, navigate their browser, or grab a screenshot of their current tab. NOT for headless fetching of public URLs (use WebFetch) — this is specifically their real, authenticated session.
---

## Quick start — orient FIRST

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser   # ← run it by this exact path
$BB whoami                          # ORIENT FIRST: which HOST + which profiles are connected
$BB health                          # is an extension connected? loaded vs repo extension_version
$BB --instance <key> open <url>     # open a NEW tab THIS session owns → returns tabId
$BB --instance <key> --tab <id> text   # cheap read of a specific tab
```

🔴 **Run `whoami` first on every fresh browser task.** Both hosts are hostname
`nixos`, and this session's loopback bridge could be either machine with several
Brave profiles — confirm the host and pick the right `--instance` before acting.

Full architecture / security model: `scripts/browser-bridge/README.md`.

## Ops

Global flags, usable before any op: `--instance <key>` (which profile),
`--tab <id>` (explicit tab), `--frame <numericId|urlSubstring>` (inside an iframe).
Result payloads land under `.result.data`.

| command | does |
|---|---|
| `whoami` | **read-only identity** (global; no `--instance`/`--tab`) — host label (`laptop`/`workbench`), connected instances (active-tab **domain** only), bridge diagnostics + `extension_version_current` |
| `health` | connected instances + count; compare each instance's loaded `extension_version` against `extension_version_current` (repo manifest) by eye — mismatch = STALE |
| `instances` | list connected instances as JSON (key, label, instanceId, active-tab url/title) |
| `open [url]` | open a NEW tab this session owns (default `about:blank`, **created in the BACKGROUND/hidden**), returns `tabId`. Idempotent. Use for multi-step work |
| `close` / `release` | close this session's owned tab / drop ownership without closing it |
| `tabs` | list open tabs (`.data.ownedTabId` flags yours) |
| `nav <url>` | navigate the owned/active tab |
| `text [selector] [--max-bytes N]` | **cheap read** — visible `innerText` (optional CSS selector), whitespace-normalized, byte-capped (default 32768; `0`=uncapped; truncation appends a note + sets `truncated`). ~98% smaller than `html` — **prefer it** |
| `html [--max-bytes N]` | `outerHTML`, same byte cap. One uncapped `html` on a heavy SPA is ~100K tokens — the cap is ON by default |
| `js '<expr>'` (alias: `eval`) | run JS in the tab, return its value. Same op on the wire either way. **Prefer `js` in a worktree-isolated agent** — Claude Code's isolation guard refuses any command containing the literal token `eval` (it matches the WORD, not the behaviour: this ships JS over loopback to Brave and touches no filesystem) |
| `screenshot [path] [--fullpage] [--data-url]` | CDP capture — **works on a BACKGROUND/occluded tab**. **Always writes a `.png`** (to `path`, else a mode-0600 temp auto-pruned after 24h) and prints `{ok,path,bytes,url,via}`; the base64 is **NEVER** printed (it cost 133K–890K tokens/call) — **`Read` the `.png`**. `--data-url` is the escape hatch |
| `frames` | list the tab's frames (`frameId`/`url`/`parentFrameId`) **including cross-origin OOPIFs** — pick a numeric `frameId` for `--frame` |
| `click <selector>` | click the element's center — **TRUSTED** CDP on the top frame, **SYNTHETIC** inside `--frame` |
| `type <text> [--selector S]` | text input — TRUSTED CDP top frame, SYNTHETIC in-frame |
| `key <Enter\|Tab\|Escape\|Backspace\|Delete\|Arrow*\|Home\|End\|Page*> [--selector S]` | one bounded keypress, same trust rules |
| `upload <selector> <path>` | populate an `<input type=file>` via CDP `DOM.setFileInputFiles` — Chrome reads the file by path, **no bytes cross the bridge**. AUDIT-LOGGED, **operator-only** (the agent gets `op_not_allowed:upload`) |
| `wake [--wait MS]` | **UN-THROTTLE a hidden/background tab with NO focus movement** — the fix for an empty or `hidden` read. ~1.5s settle, cap **6s**. **Wake once per PAGE, not per read**: the un-throttled state ends at detach, but the DOM the page rendered PERSISTS for a following cheap read |
| `text\|html\|js --wake[=MS]` | run THAT ONE read inside the woken CDP session — only when the read must observe live un-throttled state. `text`/`html --wake` read from an ISOLATED world; `js --wake` is MAIN world by definition. `--wake` + `--frame` is refused (`wake_with_frame_unsupported`) |
| `activate` | **⚠⚠ STEALS THE OPERATOR'S SCREEN — the ONE intrusive op, a LAST RESORT.** It is **NOT** the fix for a hidden/unrendered tab — that is `wake`. Use it only for something needing the REAL foreground (a browser permission prompt, a native file picker, seeing it with your own eyes), at most **once per TAB, never per read**. i3-gated; unreachable by the autonomous agent |
| `agent "<goal>"` | run the autonomous opencode browser-agent in its OWN isolated tab; returns compact `{answer,evidence,steps_used,status}` instead of page HTML → `reference/agent.md` |
| `--print-session-id` | print the derived per-session id (debug) |

## 🔴 Four traps that return a WRONG answer SILENTLY

These stay here because you only learn you needed them *after* being misled.

1. **`js`/`eval` evaluates ONE EXPRESSION, not a script.** A multi-statement body
   (`window.scrollBy(0,1400); "ok"`) returns **`null` with no error** — it looks
   like a broken bridge and isn't. Wrap it: `(function(){ …; return x })()`.
2. **Strict page CSP silently blocks the injected script — notably GitHub.** Even
   `document.title` comes back `null`, no error. **Use `text`/`html` there — they
   work**, because they don't inject script. (`chrome://`/`brave://` URLs also give
   `null` + `Cannot access a chrome:// URL`.)
3. **A background/hidden tab is THROTTLED → a shell-only DOM**, indistinguishable
   from a genuinely broken site. `open` creates tabs hidden, so this is the common
   case. Check `data.hidden` / `document.visibilityState`, then **`wake`** — never
   `activate`, and spoofing `visibilityState` does not recover the page.
   → `reference/spa-wake.md`
4. **A stale loaded extension answers `unknown_op`** for an op the CLI knows, and
   **reload ↻ in `brave://extensions` is UNRELIABLE** (the long-poll keeps the old
   service worker alive) — a **full quit-and-reopen of Brave** is the reliable fix.
   → `reference/errors.md`

**Cookies / authenticated requests.** There is no cookie op (no `cookies`
permission, on purpose), and `document.cookie` is structurally blind to **HttpOnly**
— which a session cookie always is. Don't extract the cookie; **make the request
from inside the page**, so no credential ever enters the transcript:

```bash
browser js '(async function(){ const r = await fetch("/api/thing", {credentials:"include"}); return JSON.stringify({status:r.status, body:(await r.text()).slice(0,500)}) })()'
```

The promise IS awaited. ⚠ This does **not** work on CSP-strict origins (GitHub,
Discord, …) — there, `text`/`html` are the only reads that work.

## When things look broken — triage

1. **A read is empty / half-built / `data.hidden:true`** → the tab is throttled.
   `browser wake`, then re-read. → `reference/spa-wake.md`
2. **`null` from `js`/`eval`** → multi-statement body first, then page CSP. Fall
   back to `text`/`html` before concluding the bridge is down.
3. **`unknown_op` on an op the CLI knows** → stale extension; full Brave restart.
4. **Any other unrecognised error string** → look it up in `reference/errors.md`.
5. **Never diagnose a site OUTAGE from a browser read.** "Is this broken for real
   users?" is answered by server-side evidence (RUM, metrics, pod health, an
   anonymous `curl`). A hidden-tab read once produced a confident, false, site-wide
   outage report. → `reference/spa-wake.md`

## This is the user's LIVE session

It's their real browser, not a scratch VM. Don't `nav` a tab that may hold unsaved
work (a half-typed comment, a form, a logged-in console) — `open` your own tab, or
use an obviously disposable one. If anything moved their focus, **restore it**
(`i3-msg '[id="<prev-winid>"] focus'`).

**Concurrent drivers (esp. sibling subagents) → each `open` and thread its own
`--tab <id>`.** Sibling subagents of one parent SHARE a session id (they inherit
`CLAUDE_CODE_SESSION_ID` + `$TMUX_PANE`), so without an explicit `--tab` they fight
over the SAME tab. Do **not** run a bare high-rate `eval` loop — it saturates the
single serial extension connection and gets `429 rate_limited`.
→ `reference/tabs-instances.md`

## Before you rely on it

1. `browser health` — if `extension_connected:false` or it errors, the extension
   isn't loaded/paired. Tell the user to load + pair it (`reference/security-ops.md`);
   you cannot do this for them (it's a manual Brave step).
2. **Verify it's the LIVE authenticated session**: after a read, confirm the content
   contains **logged-in-only** material (their name, account menu, inbox). If it
   looks logged-out/anonymous, the wrong tab is active or they aren't logged in —
   say so rather than proceeding.

## Reference files — load ONE only when its trigger fires

**Read them at `~/workspace/devrc/scripts/browser-bridge/reference/<file>`.** (That
exact path — only `SKILL.md` and the `browser` CLI are symlinked into
`~/.claude/skills/browser/`; `reference/` is not.)

| file | load it when… |
|---|---|
| `reference/spa-wake.md` | a read came back empty/half-built, `data.hidden:true`, an SPA is stuck "Loading…", or you're about to call a site broken |
| `reference/errors.md` | any op returned an error string you don't recognise; `unknown_op`; a reload ↻ didn't take |
| `reference/frames-cdp.md` | `frame_not_found` / `ambiguous_frame` / `oopif_*_cap` / `cdp_attach_refused`; a `--frame` read returned the TOP page; you need to read or drive inside a cross-origin iframe; the debugger banner |
| `reference/tabs-instances.md` | `ambiguous_instance` / `unknown_instance` / `superseded` / `no_owned_tab` / `owned_tab_gone`; two drivers fighting over one tab; multi-profile or multi-subagent workflows |
| `reference/css-hit-test.md` | an element is present but invisible/unclickable/painted under something; a `z-index` change "does nothing" |
| `reference/agent.md` | running `browser agent` — flags, guardrails, prereqs; it returned `blocked`; `op_not_allowed` / `nav_scheme_denied` |
| `reference/security-ops.md` | 🔴 **you are MODIFYING browser-bridge** (the live-verify-on-real-Brave gate is mandatory); the user asks whether/what it records; first-time setup or a second profile |
| `reference/x-fallback.md` | CDP `screenshot` is unsatisfactory and you must capture the raw X window (`DISPLAY`/`XAUTHORITY`, xdotool/maim) |
