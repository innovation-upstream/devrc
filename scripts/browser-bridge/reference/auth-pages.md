# Authenticated pages — cookies, in-page requests, and proving it's the live session

**Load this when:** you need an **authenticated request** against a site the user is
logged into · you were about to read or extract a **cookie** · a read looks
logged-OUT and you need to decide whether to proceed · `browser health` says
`extension_connected:false` and you're wondering what you can do about it.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Full architecture + the verification log for the measurements below:
`~/workspace/devrc/scripts/browser-bridge/README.md`.

## There is no cookie op — and `document.cookie` can't see the one that matters

The extension deliberately holds **no `cookies` permission**, so there is no
`browser cookies`. The only cookie surface is `document.cookie` via `js`/`eval`, and
it is **structurally blind to `HttpOnly`** — which a session/auth cookie essentially
always is. The cookie that matters is exactly the invisible one.

On a **CSP-strict** origin the injected script does not run at all, so a cookie read
there returns `null` with **no error** — indistinguishable from a broken bridge.

This was considered and rejected on purpose (README records the decision so it isn't
re-litigated): a real cookie op would mean a broad new permission, hard-denying it to
the autonomous browser-agent the way `upload` is, and accepting that session tokens
land in Claude transcripts, which persist on disk.

## Sanctioned pattern: don't extract the cookie — make the request INSIDE the page

An in-page `fetch(url, {credentials:"include"})` attaches the cookies (HttpOnly
included) automatically and returns only the response data. No new permission, and no
credential *value* ever enters the transcript or hits disk.

`js`/`eval` takes ONE expression, so use an async IIFE. The promise **is** awaited
(`chrome.scripting` on the top frame; CDP `awaitPromise:true` for `--frame`), so you
get the resolved value, not a pending Promise:

```bash
browser js '(async function(){ const r = await fetch("/api/thing", {credentials:"include"}); return JSON.stringify({status:r.status, body:(await r.text()).slice(0,500)}) })()'
```

Verified live 2026-07-30 against real Brave (laptop, both profiles):

- Same origin (`openrouter.ai`), same path, status only: `credentials:"include"` →
  **404**, `credentials:"omit"` → **401**. The differing status proves the HttpOnly
  session cookie WAS attached by the in-page fetch, with no cookie value crossing
  into the transcript.
- `(async function(){ return "resolved:"+(1+1) })()` → `"resolved:2"` — the async-IIFE
  shape yields a resolved value.
- CSP contrast: on a `github.com` tab `browser js 'location.host'` → `null` (no
  error) while `text`/`html` work; the identical eval on `openrouter.ai` →
  `"openrouter.ai"`.

🔴 **Limitation, stated plainly:** the in-page fetch pattern does **NOT** work on
CSP-strict origins (GitHub, Discord, …) — no injected script runs there, so an
authenticated API call through the page is unavailable and `text`/`html` are the
only reads that work.

## Before you rely on a read: prove it's the LIVE authenticated session

1. **`browser health`** — if `extension_connected:false` or it errors, the extension
   isn't loaded/paired. **Tell the user to load + pair it** (→ `reference/security-ops.md`);
   you **cannot** do this for them, it is a manual Brave step.
2. **Confirm the content is logged-in-only.** After a read, look for material that
   only an authenticated session would show — their name, an account menu, an inbox.
   If it looks logged-out or anonymous, either the wrong tab is active or they aren't
   logged in on that profile. **Say so rather than proceeding** — a logged-out read
   silently answers a different question than the one you were asked.
