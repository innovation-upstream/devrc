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
host and pick the right `--instance` first. Architecture / security model:
`~/workspace/devrc/scripts/browser-bridge/README.md`.

🔴 **Every `reference/<file>.md` named below lives at
`~/workspace/devrc/scripts/browser-bridge/reference/`** — that exact path; only
`SKILL.md` + the CLI are symlinked into `~/.claude/skills/browser/`.

## FIRST DECISION: agent or direct?

**Open-ended READ — "go find X and tell me Y" → reach for `browser agent` FIRST.**
A cheap autonomous model works in its OWN isolated tab and returns a compact
`{answer,evidence,steps_used,status}` — the page HTML (10K–100K tokens on a heavy
page) never enters YOUR context.

**Drive ops directly when** the task is **precise** (URL + selector/JS known, 1–3
ops) · **interactive** (click/type/submit/upload) · **diagnostic** (the agent is
BLIND — its tool returns no pixels; you must SEE a screenshot, or hit-test paint
order) · **secret** — agent-read pages go to
**OpenRouter/DeepSeek**: never banking, private mail, credential managers, or
anything you wouldn't hand a third party. Nor **virtualised/lazy-loaded lists**.

Ambiguous → agent first: taking over is cheap, so agent-first wins even at a low
success rate.

🔴 The AGENT's auto-`wake` covers a hidden `text`/`html` read but **never `js`** — so
ASSERT a non-zero content count in any `js` measurement. Result handling
(`blocked`/`partial`), why thin `evidence` is NOT protection, `--allow-domains`
→ `reference/agent.md`.

## Ops

Global flags, usable before any op: `--instance <key>` (which profile),
`--tab <id>` (explicit tab), `--frame <numericId|urlSubstring>` (inside an iframe).
Env defaults `$BB_INSTANCE`/`$BB_TAB`/`$BB_FRAME` (flag wins) — ⚠ an export
outlives the call → `reference/tabs-instances.md`.
Result payloads land under `.result.data`.

| command | does |
|---|---|
| `whoami` | **read-only identity** (global; no `--instance`) — host label (`laptop`/`workbench`), connected instances (active-tab **domain** only), bridge diagnostics, `extension_version_current` |
| `health` / `instances` | connected instances + count. `extension_stale` on `health`+`whoami`, NOT `instances` — ⚠ `null` = "undecidable", NOT "fine" → `reference/errors.md` |
| `ping` | **which extension CODE is loaded?** — read `buildMarker`; version+id describe the DIRECTORY. Staleness is PER PROFILE → `reference/errors.md` |
| `context` | **page metadata, no DOM read** — url/domain/path/query/title/tabId, tab-scoped. Cheapest read; ⚠ NOT a render check → `reference/read-envelopes.md` |
| `open [url] [--wake[=MS]]` | open a NEW tab this session owns (default `about:blank`, **created in the BACKGROUND/hidden**), returns `tabId`. 🔴 A re-`open` does NOT navigate — it DISCARDS your url → `reference/tabs-instances.md` |
| `close` / `release` | close this session's owned tab / drop ownership without closing it |
| `tabs` | list open tabs (`.data.ownedTabId` flags yours) |
| `nav <url> [--wake[=MS]]` | navigate the owned/active tab; it lands hidden, so `--wake` un-throttles in the SAME call |
| `text [selector] [--max-bytes N] [--annotated]` | **cheap read** — visible `innerText` (optional CSS selector), byte-capped by default. ~98% smaller than `html` — **prefer it**. `--annotated` gives per-element extraction — use it when you need a SELECTOR to click/type. Byte cap, envelope fields, `--annotated` schema → `reference/read-envelopes.md` |
| `html [--max-bytes N]` | `outerHTML`, same byte cap and envelope. One uncapped `html` on a heavy SPA is ~100K tokens — the cap is ON by default |
| `js '<expr>'` (alias: `eval`) | run JS in the tab, return its value; same wire op either way. ⚠ **prefer the `js` spelling** — an isolation guard refuses the token `eval` → `reference/errors.md` |
| `screenshot [path] [--fullpage] [--data-url] [--json]` | CDP capture — **works on a BACKGROUND/occluded tab**. **Always writes a `.png`** (to `path`, else a 0600 temp); base64 **NEVER** printed — **`Read` it**. ⚠ with `path`, stdout is the bare path, NOT JSON — add `--json`. Output modes → `reference/read-envelopes.md` |
| `frames` | list the tab's frames (`frameId`/`url`/`parentFrameId`) **incl. cross-origin OOPIFs** — pick a numeric `frameId` for `--frame` |
| `click <selector>` · `type <text> [--selector S]` · `key <Enter\|Tab\|Escape\|Backspace\|Delete\|Arrow*\|Home\|End\|Page*> [--selector S]` | the input ops — click the element's centre, type text, send one bounded keypress. All three: **TRUSTED** CDP on the top frame, **SYNTHETIC** inside `--frame` |
| `upload <selector> <path>` | fill an `<input type=file>` via CDP (no bytes cross the bridge). AUDIT-LOGGED, **operator-only** (agent → `op_not_allowed:upload`) |
| `wake [--wait MS]`, or `text\|html\|js\|nav\|open --wake[=MS]` | **UN-THROTTLE a hidden/background tab with NO focus movement** — the fix for an empty or `hidden` read. **Wake once per PAGE, not per read.** `--wake` folds un-throttle+read into one call; refused with `--frame`. Settle/cap, ISOLATED-vs-MAIN world → `reference/spa-wake.md` |
| `activate` | **⚠⚠ TAKES THE OPERATOR'S SCREEN — LAST RESORT.** The i3 raise is OPT-IN (`--focus`, auto-on on a TTY) — read the `i3` field. **NOT** the hidden-tab fix — that is `wake` → `reference/spa-wake.md` |
| `emulate <preset>\|--reset` | **device emulation** (mobile testing) on a tab you `open`ed; sticky, owned-tab-only. Presets, `--reset`, `--recreate` → `reference/emulation.md` |
| `agent "<goal>"` | the autonomous browser-agent — see **FIRST DECISION** above, then `reference/agent.md` |

## 🔴 Four traps that return a WRONG answer SILENTLY

1. **`js`/`eval` evaluates ONE EXPRESSION, not a script.** A multi-statement body
   (`window.scrollBy(0,1400); "ok"`) returns **`null` with no error** — it looks
   like a broken bridge and isn't. Wrap it: `(function(){ …; return x })()`.
2. **Strict page CSP silently blocks the injected script — notably GitHub.** Even
   `document.title` comes back `null`, no error. **Use `text`/`html` there — they
   work**, because they don't inject script. (`chrome://`/`brave://`: same `null`,
   see `reference/errors.md`.)
3. **A background/hidden tab is THROTTLED → a shell-only DOM**, indistinguishable
   from a genuinely broken site. `open` creates tabs hidden, so this is the common
   case. Check `data.hidden` / `document.visibilityState`, then **`wake`** — never
   `activate`, never a spoof. **A reload RE-throttles: re-`wake` or clicks go inert.**
   → `reference/spa-wake.md`
4. **A JS `.click()` does not open a React/Mantine popover — and the read then
   reports a confident ABSENCE.** Use the trusted **`click`** op, **ONCE** (it is a
   TOGGLE, so a stale earlier click makes the next read lie); prove it with
   `aria-expanded`. **And an OPEN menu can still hide a SECOND VIEW behind a
   chevron-row drill-in — its items are not in the DOM until you click it, so
   "not in the dropdown" is NOT "not in the UI".** → `reference/css-hit-test.md`

## When things look broken — triage

1. **A call that WORKED now fails or returns nothing** → `browser health` FIRST,
   before debugging the page or the CLI: the extension drops mid-session with no
   error. Fix: ↻ **in the profile you are driving**. A STALE BUILD is a DIFFERENT
   failure — Remove + Load unpacked, not a restart. → `reference/errors.md`
2. **Empty / half-built / `data.hidden:true` read** → throttled: `wake`, re-read.
3. **`null` from `js`** → traps 1 then 2, then `text`/`html`, before concluding the
   bridge is down. **`unknown_op`** → stale extension (1). Any other error string
   → `reference/errors.md`.
4. **Never diagnose a site OUTAGE from a browser read** — "broken for real users?"
   needs server-side evidence (RUM, metrics, pod health, an anonymous `curl`).

## This is the user's LIVE session

It's their real browser, not a scratch VM. Don't `nav` a tab that may hold unsaved
work (a half-typed comment, a form) — `open` your own tab, or an obviously
disposable one. 🔴 If ANYTHING takes their screen — `activate`, the X-fallback
capture — RECORD focus AND workspace first and restore BOTH, on failure too; the
workspace is the axis that gets left behind → `reference/spa-wake.md`.

## Reference files — load ONE only when its trigger fires

| file | load it when… |
|---|---|
| `reference/validation-prompt.md` | writing or dispatching a browser validation prompt — the standing rails, cited not retyped |
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
| `reference/sites/<host>.md` | 🔴 **READ IT BEFORE the first action on that host, not after something fails** — the CLI names it in every result envelope (`site_notes`). It carries that site's **multi-step FLOWS** (sign-in, account switching, pickers, wizards) plus the reads that lie there. Skipping it is how a one-click flow gets reported to the operator as a blocker |
