# vetr.com — two lanes, the auth seed, and the modal that eats every click

**Load this when:** a result envelope named this file in `site_notes` · you are
about to drive or read `app.vetr.com` / `api.vetr.com` / `admin.vetr.com` · you
are pointing the bridge at the LOCAL hermetic E2E stack (`127.0.0.1:5174`) ·
an authed vetr read looks logged-OUT · a vetr page renders but every click is
swallowed · you are about to conclude vetr is broken from a browser read.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Mechanism files stay authoritative for mechanism: throttling and `wake` →
`reference/spa-wake.md`; hit-testing → `reference/css-hit-test.md`; iframes and
OOPIFs → `reference/frames-cdp.md`; proving a read is the live authenticated
session → `reference/auth-pages.md`. This file is only what is true of **vetr**.

Project context lives in `~/workspace/scratch/vetr/{CLAUDE.md,OPS.md}`. The
Playwright suite this file borrows from is `vetr-app/e2e/`.

---

## 🔴 TWO LANES. Pick one before the first op.

vetr is a **pet-care marketplace running LIVE Authorize.net**. `api.vetr.com` is
real money — a booking driven there charges a real card, and a registration
driven there writes a real row (a past session had to hand-delete test users out
of the production database).

**The Playwright suite refuses to run against prod** — `assertNotProductionTarget()`
in `vetr-app/e2e/support/playwright-shared.ts` guards `E2E_BASE_URL`,
`E2E_API_BASE_URL` *and* the build-time `VITE_API_BASE_URL`. **The browser
bridge has NO such guard.** Nothing stops you. So:

| lane | target | allowed |
|---|---|---|
| **A — drive** | the hermetic stack, `http://127.0.0.1:5174` (API `:8000`) | anything: `click`, `type`, `upload`, booking, registration, checkout |
| **B — read** | `app.vetr.com`, `admin.vetr.com`, `api.vetr.com` | `text` / `html` / `context` / `screenshot` **only** |

🔴 **The registry routes on HOST, so only Lane B ever names this file.** Verified:
`vetr.com`, `app.`, `api.` and `admin.vetr.com` all resolve here; `127.0.0.1` and
`localhost` resolve to **nothing**. Lane A — the lane where you are actually
allowed to click — is therefore the lane that will **never** hand you these notes.
Load this file deliberately when you bring the stack up, and brief it into any
`browser agent` yourself (the agent never sees `site_notes` at all).

🔴 **Lane B never `click`s a money or write control.** "Just checking the button
works" on prod is a charge. If a question needs a click, bring up Lane A.

🔴 **Never diagnose a vetr OUTAGE from a browser read** — that needs pod health,
Loki/Prometheus (`obs-read`), or an anonymous `curl`, not a tab.

## Lane A — bring up the hermetic stack

```bash
cd ~/workspace/scratch/vetr/vetr-app        # ← per-host path: CONFIRM with `git -C . remote -v`
KEEP_UP=1 scripts/e2e-hermetic-nixos.sh     # docker mysql → vetr-api :8000 → SPA :5174
```

`KEEP_UP=1` runs the Playwright suite and then **leaves the stack up** for you to
drive. It also prints the fixture ids you need. `scripts/e2e-hermetic-nixos.sh`
self-provisions php84 + chromium from nix; you do not need to be in a nix shell.

The stack forces `ANET_ENDPOINT=sandbox`, so Authorize.net traffic goes to
`apitest.authorize.net` — no real money on Lane A. `PAYMENT_GATEWAY` defaults to
**authnet** (matching prod); `PAYMENT_GATEWAY=stripe` for the legacy rail.

Fixture tokens/ids come from `php artisan e2e:mint-tokens` (the script exports
them as `E2E_*`; re-mint by hand in `../vetr-api` if the shell is gone).

## 🔴 The auth seed — and why Playwright's version does NOT transfer

The SPA reads its Sanctum token from **Capacitor Preferences**, which on web is
`localStorage` under the group prefix `CapacitorStorage.`, storing the value
**verbatim** — and `NativeStorage.set` (`vetr-app/app/lib/utils.ts`) has already
`JSON.stringify`'d it. So the token is **double-quoted**:

```
localStorage["CapacitorStorage.token"] = "\"<token>\""     // note the quotes
```

Playwright seeds this with `page.addInitScript`, which runs **before app JS on
every navigation** (`e2e/fixtures/auth.ts`). **The bridge has no such hook** —
`js` only runs after the page has already booted. So the order is different, and
the order is the whole trick:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
TAB=$($BB --instance <key> open http://127.0.0.1:5174/ --wake | jq -r .result.data.tabId)

# 1. same-origin now, so localStorage is writable
$BB --instance <key> --tab $TAB js '(function(){
  localStorage.setItem("CapacitorStorage.token", JSON.stringify("<E2E_AUTH_TOKEN>"));
  localStorage.setItem("vetr-profile", JSON.stringify({state:{profile:"user"},version:0}));
  localStorage.setItem("CapacitorStorage.timezone_mismatch_dismissed",
                       JSON.stringify(Intl.DateTimeFormat().resolvedOptions().timeZone));
  return Object.keys(localStorage).length;
})()'

# 2. NOW navigate — root.tsx me() reads the token on THIS boot
$BB --instance <key> --tab $TAB nav http://127.0.0.1:5174/user --wake
```

**Seeding then reading without the second `nav` gives you the logged-OUT page**,
because the boot that mattered already happened. That failure looks exactly like
a bad token.

- **Role** is `vetr-profile` — a plain zustand-persist key, **NOT** under the
  Capacitor prefix: `{state:{profile:"user"|"vet"|"servicer"},version:0}`. A vet
  token whose `me()` has no `vet` relation redirects to `/vet/register`; that is
  the API's answer, not a seeding bug.
- Prove the seed took the way `reference/auth-pages.md` says — read the rendered
  page, not the storage you just wrote.

## 🔴 The timezone modal — a full-screen overlay that swallows every click

`root.tsx` compares the profile timezone against the device timezone on boot and
can open a **`fixed inset-0 z-[9999]` overlay** (`FeedbackModalRenderer.tsx:35`).
It intercepts every click, so the page reads fine and every input op silently
does nothing.

Playwright dodges it by pinning `timezoneId: "UTC"` to match the seeded fixtures
(they are all UTC). **The bridge is the operator's real browser and cannot pin a
timezone** — on this host (America/Winnipeg) the modal fires on every authed
Lane-A boot.

The predicate is `shouldPromptTimezoneMismatch` (`app/lib/utils/timezone.ts`) and
it has two opt-outs. **Use `timezone_mismatch_dismissed`** — it is local-only:

```
localStorage["CapacitorStorage.timezone_mismatch_dismissed"] = "\"America/Winnipeg\""
```

🔴 **Do NOT use the other opt-out, `always_update_timezone`.** It does not just
suppress the modal — it makes the app `setTimezone()` **PATCH the profile on the
server**, silently rewriting your fixture vet/owner's timezone and moving every
availability slot you were about to measure.

It only fires for an **authed** user with a differing saved profile tz, so a
logged-out walk never sees it.

## Other traps

- **`wake` every page.** The SPA boots hidden in a bridge-opened tab and a
  throttled read returns a shell — indistinguishable from a broken app. A reload
  re-throttles. → `reference/spa-wake.md`
- **First-visit gate.** `localStorage["vetr.authVisited"] === "1"` (a plain key,
  no prefix) decides `/auth/register` vs `/auth/login` for a logged-out visitor
  (`app/lib/onboarding/auth-visited.ts`). A profile that has been here before
  lands somewhere different than a fresh one — set or clear it deliberately
  rather than reporting "the app sent me to the wrong screen".
- **Check the gateway before touching checkout.** `GET /config` →
  `data.payments.gateway`, cached in `NativeStorage["config"]`;
  `resolveGateway()` defaults to `"stripe"` when unreadable
  (`app/lib/payments/gateway.ts`). Under **authnet**, card capture is the
  Authorize.net **HOSTED CIM `<iframe name=addPaymentChannel>`**
  (`app/components/payments/authnet-hosted-add-card.tsx`) — so it needs
  `--frame`, and input inside a frame is **SYNTHETIC**, not trusted CDP.
  → `reference/frames-cdp.md`
- **Mobile-first.** The app is a phone-shaped SPA that clamps to a phone column
  on desktop. `emulate` a mobile preset before judging any layout; the audit
  harnesses walk it at 390×844. → `reference/emulation.md`
- **Lane B, admin.** `admin.vetr.com` is Filament at the domain root
  (`ADMIN_PATH=/`), login `https://admin.vetr.com/login`. It is the right way to
  READ prod vet/servicer/subscription state — far cheaper than SSH — but it is
  also a write UI, so Lane B's read-only rule applies to it too.

## What the browser bridge is FOR here (and what it is not)

The Playwright suite is the regression gate: 28 spec files, and **most of it
self-skips** without the hermetic env — a bare `npm run e2e` resolves roughly 25
passed / 35 skipped. Nothing runs it automatically (GitHub Actions is dark
org-wide; Tekton runs only `vitest`/`typecheck`/`a11y`).

So the bridge's job is the **exploratory half the suite does not cover**:
reproducing a UX report, walking a flow with no spec (pool decline/expire,
cancellation-with-fee, Rx gating, subscription purchase, admin), and eyeballing a
fix before someone writes the spec. **It is not a substitute for a spec** — when a
bridge walk finds something, the deliverable is a spec in `vetr-app/e2e/`, not a
note.
