---
name: browser
description: Drive the user's LIVE, logged-in Brave browser — read the active tab's HTML, run JS in it, list/navigate tabs, screenshot the visible tab. Use when asked to look at / read / scrape / interact with a page THEY have open, act on a site they are logged into, check what is on their screen in Brave, navigate their browser, or screenshot their current tab. NOT for headless fetching of public URLs (use WebFetch).
---

## Quick start — orient FIRST

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser   # ← run it by this exact path
$BB whoami                          # ORIENT FIRST: HOST + connected profiles + extension_stale
$BB --instance <key> open <url>     # open a NEW tab THIS session owns → returns tabId
$BB --instance <key> --tab <id> text   # cheap read of a specific tab
```

🔴 **Run `whoami` first on every fresh browser task.** Both hosts are hostname
`nixos` and this bridge could be either, with several Brave profiles — confirm the
host and pick the right `--instance` before acting. Architecture / security model:
`~/workspace/devrc/scripts/browser-bridge/README.md`.

🔴 **Every `reference/<file>.md` named below lives at
`~/workspace/devrc/scripts/browser-bridge/reference/`** — that exact path; only
`SKILL.md` + the CLI are symlinked into `~/.claude/skills/browser/`.

## FIRST DECISION: agent or direct?

**Open-ended READ — "go find X and tell me Y" → reach for `browser agent` FIRST.**
A cheap autonomous model works in its OWN isolated tab and returns a compact
`{answer,evidence,steps_used,status}` — the page HTML (10K–100K tokens on a heavy
page) never enters YOUR context.

    browser agent "go to news.ycombinator.com and report the top 3 story titles"

**Drive ops directly when** the task is **precise** (URL + selector/JS known, 1–3
ops) · **interactive** (click/type/submit/upload) · **diagnostic** (the agent is
BLIND — its tool returns no pixels; you must SEE a screenshot, or hit-test paint
order) · **secret** — agent-read pages go to
**OpenRouter/DeepSeek**: never banking, private mail, credential managers, or
anything you wouldn't hand a third party. Nor **virtualised/lazy-loaded lists**.

KNOW something from it → agent. Ambiguous → agent first: taking over is cheap, so
agent-first wins even at a low success rate.

🔴 The AGENT's auto-`wake` covers a hidden `text`/`html` read but **never `eval`/`js`** — so
ASSERT a non-zero content count in any `js` measurement. Result handling
(`blocked`/`partial`), why thin `evidence` is NOT protection, and the
`--allow-domains` guardrail → `reference/agent.md`.

## Ops

Global flags, usable before any op: `--instance <key>` (which profile),
`--tab <id>` (explicit tab), `--frame <numericId|urlSubstring>` (inside an iframe).
Result payloads land under `.result.data`.

| command | does |
|---|---|
| `whoami` | **read-only identity** (global; no `--instance`/`--tab`) — host label (`laptop`/`workbench`), connected instances (active-tab **domain** only), bridge diagnostics, `extension_version_current` |
| `health` / `instances` | connected instances + count (JSON: key, label, instanceId, tab url/title). per-instance `extension_stale` on `health` + `whoami`, NOT `instances` — ⚠ `null` is "undecidable", NOT "fine"; tri-state → `reference/errors.md` |
| `ping` | **which extension CODE is loaded?** → `{pong,extensionVersion,buildMarker,id,ops}`; read `buildMarker` — version+id describe the DIRECTORY. Staleness is PER PROFILE |
| `context` | **page metadata, no DOM read** — url/domain/path/query/title/tabId, tab-scoped. Cheapest read; ⚠ NOT a render check → `reference/read-envelopes.md` |
| `open [url] [--wake[=MS]]` | open a NEW tab this session owns (default `about:blank`, **created in the BACKGROUND/hidden**), returns `tabId`. 🔴 A re-`open` does NOT navigate — it DISCARDS your url → `reference/tabs-instances.md` |
| `close` / `release` | close this session's owned tab / drop ownership without closing it |
| `tabs` | list open tabs (`.data.ownedTabId` flags yours) |
| `nav <url> [--wake[=MS]]` | navigate the owned/active tab; it lands hidden, so `--wake` un-throttles in the SAME call |
| `text [selector] [--max-bytes N] [--annotated]` | **cheap read** — visible `innerText` (optional CSS selector), byte-capped by default. ~98% smaller than `html` — **prefer it**. `--annotated` swaps flat text for per-element extraction — use it when you need a SELECTOR to click/type; works with `--frame`. Byte cap, envelope fields, `--annotated` schema → `reference/read-envelopes.md` |
| `html [--max-bytes N]` | `outerHTML`, same byte cap and same envelope. One uncapped `html` on a heavy SPA is ~100K tokens — the cap is ON by default |
| `js '<expr>'` (alias: `eval`) | run JS in the tab, return its value; same op on the wire either way. **Prefer the `js` spelling in a worktree-isolated agent** — Claude Code's isolation guard refuses any command containing the literal token `eval` |
| `screenshot [path] [--fullpage] [--data-url]` | CDP capture — **works on a BACKGROUND/occluded tab**. **Always writes a `.png`** (to `path`, else a 0600 temp) and prints `{ok,path,bytes,url,via}`; the base64 is **NEVER** printed — **`Read` the `.png`**. `--data-url` is the escape hatch |
| `frames` | list the tab's frames (`frameId`/`url`/`parentFrameId`) **incl. cross-origin OOPIFs** — pick a numeric `frameId` for `--frame` |
| `click <selector>` · `type <text> [--selector S]` · `key <Enter\|Tab\|Escape\|Backspace\|Delete\|Arrow*\|Home\|End\|Page*> [--selector S]` | the input ops — click the element's centre, type text, send one bounded keypress. All three: **TRUSTED** CDP on the top frame, **SYNTHETIC** inside `--frame` |
| `upload <selector> <path>` | fill an `<input type=file>` via CDP — Chrome reads the file BY PATH, **no bytes cross the bridge**. AUDIT-LOGGED, **operator-only** (agent → `op_not_allowed:upload`) |
| `wake [--wait MS]`, or `text\|html\|js\|nav\|open --wake[=MS]` | **UN-THROTTLE a hidden/background tab with NO focus movement** — the fix for an empty or `hidden` read. **Wake once per PAGE, not per read.** `--wake` folds un-throttle+read into one call; refused with `--frame`. Settle/cap, the once-per-page rule, ISOLATED-vs-MAIN world → `reference/spa-wake.md` |
| `activate` | **⚠⚠ STEALS THE OPERATOR'S SCREEN — the ONE intrusive op, a LAST RESORT.** It is **NOT** the fix for a hidden/unrendered tab — that is `wake`. Read `reference/spa-wake.md` BEFORE using it |
| `emulate <preset>\|--reset` | **device emulation** (mobile testing) on a tab you `open`ed; sticky, owned-tab-only. Presets, `--reset`, `--recreate` → `reference/emulation.md` |
| `agent "<goal>"` | the autonomous browser-agent — see **FIRST DECISION** above, then `reference/agent.md` |

## 🔴 Four traps that return a WRONG answer SILENTLY

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
   **A reload RE-throttles: re-`wake` or clicks go silently inert.**
   → `reference/spa-wake.md`
4. **A JS `.click()` does not open a React/Mantine popover — and the read then
   reports a confident ABSENCE.** Use the trusted **`click`** op; click **ONCE** —
   it is a TOGGLE, so a stale earlier click makes the next read lie — and read
   `aria-expanded` to prove it opened. Not selector-reachable? **`screenshot`** it.
   → `reference/css-hit-test.md`

## When things look broken — triage

1. **A call that WORKED now fails, errors, or returns nothing** → re-run `browser
   health` BEFORE debugging the page or the CLI; the extension drops mid-session
   (`extension_connected:false`) with no user action and no error. Fix: ↻ **in the
   profile you are driving** (`brave://extensions` is per-profile). A STALE BUILD is
   a DIFFERENT failure — a Brave restart does NOT clear it; that needs per-profile
   Remove + Load unpacked. → `reference/errors.md`
2. **A read is empty / half-built / `data.hidden:true`** → the tab is throttled.
   `browser wake`, then re-read. → `reference/spa-wake.md`
3. **`null` from `js`/`eval`** → trap 1, then trap 2. Fall back to `text`/`html`
   before concluding the bridge is down. **`unknown_op`** on an op the CLI knows →
   stale extension, see 1. Any other error string → `reference/errors.md`.
4. **Never diagnose a site OUTAGE from a browser read** — "broken for real users?"
   needs server-side evidence (RUM, metrics, pod health, an anonymous `curl`).
   → `reference/spa-wake.md`

## This is the user's LIVE session

It's their real browser, not a scratch VM. Don't `nav` a tab that may hold unsaved
work (a half-typed comment, a form) — `open` your own tab, or an obviously
disposable one. 🔴 If ANYTHING takes their screen — `activate`, the X-fallback
capture — RECORD focus AND workspace first, then restore BOTH at the end, on
failure too. Focus alone leaves them on YOUR workspace, which is the axis that
actually gets taken. Exact commands → `reference/spa-wake.md`.

## Reference files — load ONE only when its trigger fires

Paths as stated in Quick start.

| file | load it when… |
|---|---|
| `reference/spa-wake.md` | a read came back empty/half-built, `data.hidden:true`, an SPA is stuck "Loading…", or you're about to call a site broken |
| `reference/read-envelopes.md` | a read's exact envelope fields; `context` vs `text`; `text --annotated` + the `attrs` it returns; getting a SELECTOR out of a read |
| `reference/errors.md` | any op returned an error string you don't recognise; `unknown_op`; a reload ↻ didn't take |
| `reference/frames-cdp.md` | `frame_not_found` / `ambiguous_frame` / `oopif_*_cap` / `cdp_attach_refused`; a `--frame` read returned the TOP page; reading or driving inside a cross-origin iframe; the debugger banner |
| `reference/tabs-instances.md` | `ambiguous_instance` / `unknown_instance` / `superseded` / `no_owned_tab` / `owned_tab_gone`; two drivers fighting over one tab; concurrent subagents SHARE a session id; a re-`open` ignored your url |
| `reference/css-hit-test.md` | an element is present but invisible/unclickable/painted under something; a `z-index` change "does nothing"; a `data-testid` selector matches NOTHING; the wrong control looks primary; text "vanished" |
| `reference/emulation.md` | `emulate` BEFORE `nav` (else no touch API); presets, overrides, errors |
| `reference/agent.md` | running `browser agent` — flags, guardrails, prereqs; it returned `blocked`; `op_not_allowed` / `nav_scheme_denied` |
| `reference/auth-pages.md` | an authenticated request; you were about to read a cookie; a read looks logged-OUT; `extension_connected:false` |
| `reference/security-ops.md` | 🔴 **you are MODIFYING browser-bridge** (the live-verify-on-real-Brave gate is mandatory); the user asks whether/what it records; first-time setup or a second profile |
| `reference/x-fallback.md` | CDP `screenshot` is unsatisfactory and you must capture the raw X window (`DISPLAY`/`XAUTHORITY`, xdotool/maim) |
| `reference/sites/<host>.md` | you are driving a site that has one — the CLI names it in the result envelope |
