# localhost — the operator's LOCAL DEV SERVERS, and why a 404 here usually is not a bug

**Load this when:** a result envelope named this file in `site_notes` · you are about
to drive `http://localhost:<port>` · a local page 404s, renders an onboarding wizard,
or looks logged-out and you are about to call the app broken.

---

## 🔴 `localhost` is NOT one site — identify the PROJECT before anything else

Every project on this machine serves `localhost`, so unlike every other file in this
directory the host name tells you nothing. **`context` first** and read the port and
the `<title>`; the flows below are keyed to the project, not to the host.

```bash
$BB --instance work context      # url/port/title, no DOM read — the cheapest identification
```

⚠ **The port is not stable either.** A dev server takes the next free port when its
default is occupied, so civitai commonly runs on **3001** rather than 3000 (a
`kubectl port-forward` frequently holds 3000 on the workbench). Identify by TITLE +
the routes present, never by assuming a port belongs to a project.

## 🔴 This is the OPERATOR'S live browser and their dev server

Two things follow, and both have bitten:

- **Do not `nav` their tab.** A local dev tab routinely holds a half-filled form or a
  state that took a while to reach. `open` your own tab, or ask.
- **Never diagnose the PROJECT from a browser read alone.** A local server can be
  mid-recompile, throttled, or serving a stale bundle. `data.hidden:true` ⇒ `wake`
  (never `activate`). A read that raced the first compile of a route returns a nav
  error that looks exactly like a dead server — retry once warm before concluding.

---

# civitai (title `… — Civitai`, typically `:3001`)

## 🔴 A 404 on `/apps*` is the DEFAULT for a normal viewer — it is a GATE, not a break

`/apps` is flag-gated (`resolveAppsPageAccess` → `hasAppsStoreAccess`) and **404s for
anyone the flags do not admit**, deliberately, so the surface cannot be enumerated.
`/apps/mine`, `/apps/review` and friends additionally need a session. So:

**Read the session BEFORE concluding anything** — it is one request and it is
authoritative:

```bash
$BB --instance work js '(function(){return fetch("/api/auth/session").then(function(r){return r.text()})})()'
# or simply open http://localhost:<port>/api/auth/session
```

`{"user":{…,"isModerator":…}}` ⇒ signed in. An empty/`null` body ⇒ signed out, and a
404 on `/apps/mine` is then EXPECTED, not a defect.

## 🔴 Login is the AUTH HUB, not next-auth — and the mail never leaves the machine

The main app is a verify-only spoke; sign-in lives in a separate hub on **:5173**.
There is no password form: enter an email, the hub SENDS A MAGIC LINK, and locally
that mail goes to a catcher (a maildev container, or a local SMTP sink writing to a
file — whichever the operator set up). The flow:

1. `open http://localhost:5173/login`
2. `type` an address into the email field, `click` the "Email me a login link" button
3. read the link out of the local mail catcher (NOT from the browser)
4. `nav` your own tab to that URL

🔴 **A magic-link token is SINGLE-USE.** Re-visiting a link you already consumed
lands back on `/login`, which reads as "login is broken". Request a fresh one.

## 🔴 A 200 that renders "Welcome!" is the ONBOARDING WIZARD, not the route

If a signed-in local user has an incomplete `onboarding` bitmask, every gated route
renders the onboarding flow (often with a hydration error in the console) instead of
the page you asked for. It looks exactly like the route being broken. The fix is a
database flip plus a **re-login** — the session is cached, so changing the row is not
enough on its own. That is the project's business, not the browser's: report what you
see and hand it back rather than trying to drive the wizard.

## 🔴 An App Block iframe stuck on its boot skeleton is usually NOT the block

`/apps/run/<slug>` mounts the block in an iframe whose source is an EXTERNAL origin
named in the block's manifest — not the civitai app. Locally that origin is often not
served at all, so the host sits on its boot skeleton forever. A visible
**"Couldn't authenticate this app"** is a different failure again: the app's token
mint is unconfigured server-side, and it returns a hard 503 that surfaces as an
auth-shaped message. Neither is a browser problem; do not report either as a
rendering bug without checking the server log.

## Useful, stable selectors on the store

`[data-testid="apps-listing-grid"]` (the grid; read `gridTemplateColumns` for the
column count) · `[data-testid="apps-listing-grid-col"]` (one per card) ·
`[data-testid="apps-listing-recommend-rollup"]` · `[data-testid="apps-listing-card-beta"]`.

⚠ **These exist in DEV only.** `next.config.mjs` strips `data-testid` under
`NODE_ENV=production` (`reactRemoveProperties`), so a selector that works locally
matches NOTHING on the deployed site — never carry a testid selector from here into a
flow against the real host.
