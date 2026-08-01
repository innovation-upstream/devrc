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
`nixos` and this bridge could be either, with several Brave profiles — confirm the
host and pick the right `--instance` before acting.

Full architecture / security model: `scripts/browser-bridge/README.md`.

## FIRST DECISION: agent or direct?

**Open-ended READ — "go find X and tell me Y" → reach for `browser agent` FIRST.**
A cheap autonomous model works in its OWN isolated tab and returns a compact
`{answer,evidence,steps_used,status}` — the page HTML (10K–100K tokens on a heavy
page) never enters YOUR context.

    browser agent "go to news.ycombinator.com and report the top 3 story titles"

**Drive ops directly when** the task is **precise** (URL + selector/JS known, 1–3
ops) · **interactive** (click/type/submit/upload) · **diagnostic** (you must SEE a
screenshot, or hit-test paint order) · **secret** — agent-read pages go to
**OpenRouter/DeepSeek**: never banking, private mail, credential managers, or
anything you wouldn't hand a third party. Nor **virtualised/lazy-loaded lists**.

SEE the page → drive. KNOW something from it → agent. Ambiguous → agent first;
taking over costs ~200 tokens, so agent-first wins even at a low success rate.

**Measured** (14 goals, each run ONCE; laptop `.155`, `personal`, opencode 1.18.4,
`deepseek-v4-flash`, 2026-07-31): **13/14 correct**, ~**$0.0025**/run, median
**22.65s**. One run per goal = wide CI; read it as "≈0.9", not a rate.

`blocked` → non-zero exit, take over. `partial` → read `evidence`, drive the rest.
Thin `evidence` is a weak smell, **NOT** protection against a wrong answer — the
one wrong run quoted its unrendered page verbatim, and the check would have flagged
a *correct* run. What protects you: the deterministic auto-`wake` on a hidden
`text`/`html` read (that was the one failure; fixed), and cheap escalation — if the
answer depends on rendered SPA state, confirm with ONE `text --wake`. **Ignore
`steps_used`** (undercounted, 5/14). Keep `--allow-domains` TIGHT *and* list the
frame hosts you expect. → `reference/agent.md`

## Ops

Global flags, usable before any op: `--instance <key>` (which profile),
`--tab <id>` (explicit tab), `--frame <numericId|urlSubstring>` (inside an iframe).
Result payloads land under `.result.data`.

| command | does |
|---|---|
| `whoami` | **read-only identity** (global; no `--instance`/`--tab`) — host label (`laptop`/`workbench`), connected instances (active-tab **domain** only), bridge diagnostics + `extension_version_current` |
| `health` | connected instances + count, each with an explicit `extension_stale` verdict (`true`/`false`; `null` = undecidable) vs `extension_version_current` |
| `ping` | **which extension build+dir is loaded?** → `{pong,extensionVersion,id,ops}`; older build → `unknown_op`. The only deterministic answer after a ↻ reload |
| `instances` | connected instances as JSON (key, label, instanceId, active-tab url/title) |
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
| `activate` | **⚠⚠ STEALS THE OPERATOR'S SCREEN — the ONE intrusive op, a LAST RESORT.** It is **NOT** the fix for a hidden/unrendered tab — that is `wake`. Read `reference/spa-wake.md` BEFORE using it |
| `emulate <preset>\|--reset` | **device emulation** (mobile testing) on a tab you `open`ed. Sticky; owned-tab-only → `reference/emulation.md` |
| `agent "<goal>"` | the autonomous browser-agent — see **FIRST DECISION** above, then `reference/agent.md` |

## 🔴 Three traps that return a WRONG answer SILENTLY

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

## When things look broken — triage

1. **A call that WORKED now fails, errors, or returns nothing** → re-run `browser
   health` BEFORE debugging the page or the CLI. The extension can drop
   mid-session (`extension_connected:false`) with no user action and no error
   (measured twice in one session, 2026-07-31). ⚠ The ↻ fix must be done **in the
   profile you are driving** — `brave://extensions` is per-profile, and `health`'s
   connected count can be coming from a DIFFERENT instance. → `reference/errors.md`
2. **A read is empty / half-built / `data.hidden:true`** → the tab is throttled.
   `browser wake`, then re-read. → `reference/spa-wake.md`
3. **`null` from `js`/`eval`** → trap 1, then trap 2. Fall back to `text`/`html`
   before concluding the bridge is down.
4. **`unknown_op` on an op the CLI knows** → stale extension. Reload ↻ is
   UNRELIABLE (the long-poll keeps the old worker alive) — full Brave restart.
5. **Any other unrecognised error string** → look it up in `reference/errors.md`.
6. **Never diagnose a site OUTAGE from a browser read** — a hidden-tab read once
   produced a confident, false, site-wide outage report. "Broken for real users?"
   needs server-side evidence (RUM, metrics, pod health, an anonymous `curl`).
   → `reference/spa-wake.md`

## This is the user's LIVE session

It's their real browser, not a scratch VM. Don't `nav` a tab that may hold unsaved
work (a half-typed comment, a form) — `open` your own tab, or use an obviously
disposable one. If anything moved their focus, **restore it**
(`i3-msg '[id="<prev-winid>"] focus'`).

**Concurrent drivers (esp. sibling subagents, which SHARE one session id) → each
`open` and thread its own `--tab <id>`**; and no high-rate `js` loop (the extension
connection is serial → `429 rate_limited`). → `reference/tabs-instances.md`

## Reference files — load ONE only when its trigger fires

**Read them at `~/workspace/devrc/scripts/browser-bridge/reference/<file>`** — that
exact path; only `SKILL.md` + the `browser` CLI are symlinked into
`~/.claude/skills/browser/`, `reference/` is not.

| file | load it when… |
|---|---|
| `reference/spa-wake.md` | a read came back empty/half-built, `data.hidden:true`, an SPA is stuck "Loading…", or you're about to call a site broken |
| `reference/errors.md` | any op returned an error string you don't recognise; `unknown_op`; a reload ↻ didn't take |
| `reference/frames-cdp.md` | `frame_not_found` / `ambiguous_frame` / `oopif_*_cap` / `cdp_attach_refused`; a `--frame` read returned the TOP page; you need to read or drive inside a cross-origin iframe; the debugger banner |
| `reference/tabs-instances.md` | `ambiguous_instance` / `unknown_instance` / `superseded` / `no_owned_tab` / `owned_tab_gone`; two drivers fighting over one tab; multi-profile or multi-subagent workflows |
| `reference/css-hit-test.md` | an element is present but invisible/unclickable/painted under something; a `z-index` change "does nothing"; a `data-testid` selector matches NOTHING on a prod page |
| `reference/emulation.md` | `emulate` BEFORE `nav` (else no touch API); presets, overrides, errors |
| `reference/agent.md` | running `browser agent` — flags, guardrails, prereqs; it returned `blocked`; `op_not_allowed` / `nav_scheme_denied` |
| `reference/auth-pages.md` | an authenticated request; you were about to read a cookie; a read looks logged-OUT; `extension_connected:false` |
| `reference/security-ops.md` | 🔴 **you are MODIFYING browser-bridge** (the live-verify-on-real-Brave gate is mandatory); the user asks whether/what it records; first-time setup or a second profile |
| `reference/x-fallback.md` | CDP `screenshot` is unsatisfactory and you must capture the raw X window (`DISPLAY`/`XAUTHORITY`, xdotool/maim) |
