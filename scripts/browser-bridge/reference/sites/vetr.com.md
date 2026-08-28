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
drive. `scripts/e2e-hermetic-nixos.sh` self-provisions php84 + chromium from nix;
you do not need to be in a nix shell. Add `-- --grep <spec>` to cut the ~11-minute
full run down to one spec when you only want the stack.

On the **authnet** rail (the default, matching prod) the stack forces
`ANET_ENDPOINT=sandbox`, so Authorize.net traffic goes to `apitest.authorize.net`
— no real money.

🔴 **That protection is authnet-ONLY. `PAYMENT_GATEWAY=stripe` has NO equivalent
check:** `e2e-hermetic-nixos.sh:243-250` copies `STRIPE_SECRET_KEY` out of your
environment into the API's `.env` **verbatim, with no test-mode assertion**. Prod
runs `sk_live`/`pk_live`, and Lane A is the lane where you are told to click
Book. So on the stripe rail, **export the test keys first** —
`~/.config/vetr/stripe-test.env` (`pk_test`/`sk_test`/`whsec`) — and never run it
with a live key in the shell. Keyless works for every non-payment flow — the key
is simply left **empty** (`vetr-api/config/services.php` has no default), and the
payment/pool-loop specs self-skip.

🔴 **You must mint your own token — the script does NOT give you one.** It
captures `e2e:mint-tokens`, validates the JSON with `jq empty` and **discards
it** (`:260-261`), then `export`s the ids into its **own** process (`:264-276`).
It is invoked, not sourced, so nothing reaches your shell, and the `KEEP_UP`
message prints only ports and the container name. Without a token the auth seed
below has no input. Mint one yourself — there is no system PHP on NixOS, so
borrow the one the script already built:

```bash
# /tmp/vetr-e2e-tools is the script's own -o output link (e2e-hermetic-nixos.sh:126,139).
# Use it, NOT a /nix/store glob: the glob's lexicographic winner is an arbitrary
# PAST build (measured: 8.4.19 vs the live 8.4.24) and, unlike this link, is not a
# GC root. Requires the KEEP_UP stack to be up — it needs the .env the script wrote.
(cd ~/workspace/scratch/vetr/vetr-api && /tmp/vetr-e2e-tools/bin/php artisan e2e:mint-tokens)
```

🔴 **Re-minting REVOKES the token already in your tab.** `E2EMintTokens.php:77`
does `$user->tokens()->where('name','e2e')->delete()` for every fixture before
issuing new ones — so the tab you seeded goes 401 and reads logged-OUT, which this
file elsewhere teaches you to read as a seeding-order mistake. **Re-seed after
every mint.** (`--seed` *is* opt-in, so a bare run leaves seeded rows alone — but
it is not otherwise inert: it also touches Stripe/CIM to provision the saved card,
`:112-138`.)

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

**Verified end-to-end 2026-08-27, hermetic stack, laptop, Brave profile `work`:**
on a fresh tab the recipe lands the **fully-rendered authed dashboard** (1232
chars of `innerText` — "Quick Actions / My Vet Appointments / … / Switch/Add
profile") with the **complete data fan-out** (`/api/user/pets`, `/api/indicators`,
`/api/vets/popular`, `/api/user/subscriptions`, …). Negative control: seeding
without the second `nav` leaves the page logged-out. Token confirmed
independently (`/api/auth/me` → `E2E Owner`, id 1, tz `UTC`).

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
does nothing. ⚠ That renderer is **shared by eleven `<FeedbackModal>` callers** —
this modal is identified by its **title**, never by its z-index (see "`z-index:
9999` does NOT identify the timezone modal" below).

**Measured, both controls.** WITHOUT the opt-out: the modal fires — "Timezone
Mismatch Detected / Your device timezone (America/Winnipeg) doesn't match…" — its
rect is `1124×1361`, **exactly the viewport**, `pointer-events: auto`,
`z-index: 9999`, and a hit-test of **all 35 in-viewport controls** returns the
modal as the topmost element for **every one of them**. WITH
`timezone_mismatch_dismissed` seeded: no modal, dashboard renders normally.

Playwright dodges it by pinning `timezoneId: "UTC"` to match the seeded fixtures
(they are all UTC). **The bridge is the operator's real browser and cannot pin a
timezone** — on this host (America/Winnipeg) the modal fires on every authed
Lane-A boot.

The predicate is `shouldPromptTimezoneMismatch` (`app/lib/utils/timezone.ts:16-30`)
and it has two opt-outs. **Use `timezone_mismatch_dismissed`** — it is local-only.

🔴 **Write the DEVICE's zone, computed at runtime — never a literal.** The
predicate suppresses only when `dismissedTz === deviceTz` (`timezone.ts:27`), so a
hardcoded zone copied from this file silently does **nothing** on any host in a
different one, and you get the click-eating modal you were just told you had
disabled:

```
// right — this is the form used in the seed recipe above
localStorage.setItem("CapacitorStorage.timezone_mismatch_dismissed",
                     JSON.stringify(Intl.DateTimeFormat().resolvedOptions().timeZone));
```

🔴 **Do NOT use the other opt-out, `always_update_timezone`.** It does not just
suppress the modal — it makes the app `setTimezone()` **write the profile back to
the server** (`authStore.ts` → `profileApi.editProfile`, a `POST /user/profile`),
silently rewriting your fixture vet/owner's timezone and moving every
availability slot you were about to measure.

It only fires for an **authed** user with a differing saved profile tz, so a
logged-out walk never sees it.

## 🔴 A TIRED TAB STALLS PRE-HYDRATION — and it reads exactly like "vetr is broken"

The single most expensive thing that happened while writing this file. **A tab
reused across many `nav`s stopped rendering any route.** It reproduced on
`/choose-experience` (logged-OUT, 4 Playwright tests against that same origin),
on `/user`, and on `/user/appointments`, across 6+ attempts with `--wake` up to
10s and `localStorage.clear()`. **It was written up as a vetr defect. It is not
one** — a `close` + fresh `open` of the *same URL* rendered everything
immediately.

🔴 **Identify it by the FALLBACK, not by emptiness.** SSR is off, so until the JS
bundle loads and React mounts, the only thing painted is `HydrateFallback`
(`vetr-app/app/root.tsx:377-398`): `data-testid="app-boot-skeleton"`,
`aria-hidden="true"`, `className="relative flex h-dvh flex-col"`, **exactly 3
children**, and **no text nodes anywhere**. That is what a stalled tab is showing.
It is trivially mistaken for a mounted app shell — the observation that started
this whole mess was literally "`DIV.relative flex h-dvh flex-col`, kids=3, so
React mounted". It had not. **Query `[data-testid=app-boot-skeleton]`: present ⇒
the bundle never executed, and nothing you read afterwards is about the route.**

Ruled out, so nobody re-runs them: **not the server** (all paths return an
identical 5176-byte SPA-fallback `index.html`, 200, `cmp` clean); **not
throttling** (`activate`d — `visibilityState: visible`, animations advancing,
still stalled); **not the seeded keys**; **not the token**; **not GTM/Brave
Shields** (`TagManager.initialize` is try/caught non-fatal, `root.tsx:288`);
**not the error boundary** (it renders a visible "Oops!", `root.tsx:400`).

### 🔴 vetr has TWO full-screen boot overlays. Tell them apart by TEXT.

Conflating these produced two wrong findings in a row, in opposite directions.

| | `HydrateFallback` | brand `Splash` |
|---|---|---|
| where | `vetr-app/app/root.tsx:377-398` | `app/components/splash.tsx:105-118`, mounted via `components/GeneralComponents.tsx:1,10` |
| identify by | `[data-testid=app-boot-skeleton]` | `[data-testid=brand-splash]` |
| looks like | grey shimmer bars on the page background | **bright orange→lime** full-viewport gradient (`.bg-splash` = `from-[#ff5301] to-[#d1ff27]`, `app/app.css:361-363`) — *not* dark |
| text | **none** (`aria-hidden`) | renders **"Vetr"** as `<span>` per letter |
| when | before the JS bundle executes | **after** hydration, first visit per tab (`sessionStorage.splashSeen`, a raw key — no Capacitor prefix) |
| holds for | until hydration | **depends on the ENTRY path, not on auth** — see below |
| blocks clicks | it *is* the page | **no** — `pointer-events-none`, so a hit-test can never return it. But do **not** infer the converse: see below |

🔴 **The splash's duration is keyed on LANDING vs non-landing — never on authed
vs logged-out.** `landingEntry` is the **entry** URL matching
`LANDING_PREFIXES = ["/user/vets", "/user/servicers", "/user/shop"]`
(`splash.tsx:17`), captured once at first mount:

Matching is exact-or-slash (`:20-22`), so `/user/vetsomething` does **not** count.
`debounceMs` (200, `:29`) is added to **both** branches (`:70,78` —
`remaining + debounceMs`), so the real floors are:

| entry | constant | **real floor** | waits for in-flight queries? |
|---|---|---|---|
| **landing** (`/user/vets/:id`, `/user/servicers…`, `/user/shop…`) | 300ms (`LANDING_FLOOR_MS`, `:18,39`) | **500ms** | **no** — `navigation.state === "idle"` only (`:60-61`) |
| anything else (incl. `/auth/login`, `/user`) | 2000ms (`minimumVisibleTime`, `:29`) | **~2200ms** | **yes** — `&& isFetching === 0` (`:62`) |

Note both landing paths are **authed** routes, so an auth-keyed rule would get them
exactly backwards. The floors are minimums, not durations — a non-landing entry
waits as long as queries stay in flight.

`landingEntry` is captured **once at first mount** from the entry URL
(`useRef`, `:36-38`), so navigating in or out of a landing route later does **not**
change the behaviour. `<Splash />` is rendered with no props
(`GeneralComponents.tsx:10`), so the `:29` defaults are what actually apply.

⚠ **The testid outlives the visible splash by the 500ms fade.** `visible→false`
applies `opacity-0` and arms a 500ms unmount timer at the same moment (`:94-100`,
`transition-opacity duration-500`), so for that 500ms
`[data-testid=brand-splash]` is still in the DOM, mid-fade — and its text still
counts in `innerText`, because nothing sets `display:none`, `visibility:hidden` or
`aria-hidden` on it.

🔴 **The brand `Splash` is LIVE — do not read anywhere that it was removed.** An
earlier revision of this file claimed it "was *replaced* by" the skeleton, citing
`root.tsx:378-384`. **That is false.** That comment says the pre-hydration paint
*used to be blank, which read as* a ~2s dark "Vetr" splash — it is about the
**blank**, not about `splash.tsx`, which is still imported and still rendered. The
built `index.html` also contains "Vetr" **twice** (`<title>` and
`apple-mobile-web-app-title`), neither of them rendered text.

This matters operationally because **the remedy above is "open a fresh tab"** —
and a fresh tab is exactly the case that gets the brand splash: an opaque
full-viewport orange→lime overlay which, on a **non-landing** entry, holds ~2.2s
*and* waits for every in-flight query. That is the state most easily mistaken for a
stall. On a **landing** entry it is a ~500ms flash instead — so the same walk looks
different depending only on which URL you entered at.

### 🔴 `z-index: 9999` does NOT identify the timezone modal

An earlier revision of this file said a `z-9999` blocker "is **always** the timezone
modal". **False, and it sends you to a remedy that cannot work.** There are (at
least) **two distinct z-9999 `pointer-events: auto` surfaces** in a browser tab.

**(a) The shared `FeedbackModalRenderer` backdrop** (`FeedbackModalRenderer.tsx:35`).
The timezone modal (`root.tsx:334`) is *one* of **eleven** `<FeedbackModal>` callers
— the other ten include the exact surfaces this file routes you to:

- `routes/user/vets/vet-view.tsx:1764` — `/user/vets/:id`, the landing route above
- `routes/user/bookings/booking-checkout.tsx:88` and
  `routes/user/appointments/appointment-checkout.tsx:143` — the checkout screens the
  gateway trap below sends you into
- plus `bookings/index.tsx`, `appointments/index.tsx`, `servicer-service-view.tsx`,
  `my-pets-create.tsx`, `pages/contact.tsx`, `professional-report.tsx`,
  `PetOwnerHomeBookingCard.tsx`

**(b) The react-toastify container — and in a bridge walk this is the LIKELIER
one.** `<ToastContainer>` is mounted unconditionally on every route
(`root.tsx:161`), and `ReactToastify.css` sets `--toastify-z-index: 9999` (`:29`,
applied `:52`) with **no `pointer-events` rule anywhere in the stylesheet** ⇒
default `auto`. The axios interceptor fires `toast.error` on **any** API error
(`app/lib/api/axios.tsx:151,192,197`), and vetr passes `closeOnClick={false}` +
`autoClose={5000}` (`root.tsx:161-167`). So an API error parks a clickable
`position: fixed`, top-right, z-9999 box over the page for 5s. Bounded, at least:
`--toastify-container-width: fit-content` (`:15`, applied `:55`), so with no toast up
the container is zero-size and intercepts nothing.

⚠ **`UpdateApp.tsx:9` is `fixed inset-0 z-[9999]` with no `pointer-events-none`, but
you will NEVER meet it in a browser** — `setUpdateStoreUrl` has exactly one call site
(`root.tsx:245`) behind `if (!Capacitor.isNativePlatform()) return;` (`root.tsx:236`),
plus `isProduction` and an outdated native build. And where it *does* fire it is an
early `return` replacing the whole tree (`root.tsx:320-322`), not an overlay — so it
cannot produce the "page reads fine, clicks do nothing" symptom at all. Listed only
because an earlier revision of this file offered it as the non-`FeedbackModal`
example; it is a phantom.

🔴 **So read the modal's TITLE, never its z-index.** Only
"Timezone Mismatch Detected" is cleared by `timezone_mismatch_dismissed`; a
success/error `FeedbackModal` needs its own dismiss (`title` is a required prop and
renders as plain text, `FeedbackModal.tsx:8` / `FeedbackModalRenderer.tsx:60`, so
`innerText` reads it); a toast has no title row at all — check for
`.Toastify__toast-container`. What the splash row above *does* license is the one-way
inference: the splash is `pointer-events-none`, so whatever a hit-test returns, it
is not the splash.

So, precisely: a read with **zero `innerText`** does rule the brand splash out —
splash renders text. What it does **not** rule out is stalled hydration, because
`HydrateFallback` is textless too. Two different textless-vs-texted states; check
for both by `data-testid`, never by emptiness.

🔴 **So: if a vetr route reads blank, `close` the tab and `open` a fresh one
BEFORE forming any theory.** Why a reused tab reaches that state is a
bridge-level question, not a vetr one.

### 🔴 And your blankness detector is probably lying

Three instruments each gave a confident wrong answer here. **All three are
generic** — they belong to the mechanism files, and this list is the vetr-shaped
illustration, not the home of the rule. The `elementFromPoint` one is now written
up where it belongs, in `reference/css-hit-test.md`.

- **`innerText` needs layout**; a backgrounded/occluded tab returns `""` for a
  page that is rendering fine. Use `textContent` for "is anything in the DOM",
  and a **`screenshot`** when you need to see which state it is in. (Cheaper and
  deterministic for the boot states specifically: query the two `data-testid`s in
  the table above.)
- **A button/link count of 0 means nothing** — a pre-hydration skeleton has no
  text *and* no controls, by construction. Measured: the chooser renders three
  role cards and still counted 0 `button,a`.
- **`elementFromPoint` returns `null` for an off-viewport point**, which reads
  identically to "covered by an overlay". vetr renders an off-canvas menu at
  negative x, so its controls sit at e.g. `x = -249`. **Filter to in-viewport
  centres first**, then hit-test — otherwise you will "discover" a blocking
  overlay that is not there.

### Two `js` behaviours that cost real time here

- **A returned Promise is not awaited.** An `async` expression comes back empty,
  with no error — consistent with `SKILL.md`'s "evaluates ONE EXPRESSION". Use one
  self-contained *synchronous* expression, or `curl` the API directly.
- ⚠ **`window` state may not survive between two consecutive `js` calls.**
  Observed here (`window.__x = …`, then reading it back returned nothing).
  **`--frame` has TWO eval branches and only one of them explains it** — so this is
  not a general answer:
  - **same-process** frame → `Page.createIsolatedWorld` with a **fresh world per
    call** (`extension/service_worker.js:537-544`) ⇒ globals invisible, cross-call
    state gone by design.
  - **cross-origin OOPIF** → evaluated in that frame's own flat session's
    **default (MAIN) context** (`:546-549`) ⇒ globals *do* persist.

  ⚠ **vetr's authnet hosted-CIM iframe is the OOPIF case, not the isolated one** —
  it is served from `accept.authorize.net`
  (`app/components/payments/authnet-hosted-add-card.tsx:12-14`), cross-site to the
  app origin. So `--frame` does **not** explain the loss for the one iframe this
  file sends you into. Without `--frame` at all, `js` is **MAIN** world
  (`service_worker.js:864-871`; under `--wake` the branch is `:849`, asserted in the
  comment at `:841-846`).

  Net: the observation is **still unexplained** for a top-frame call, which is what
  it was made on. `reference/frames-cdp.md:45-46` is authoritative on the split; the
  ISOLATED-world line in the CLI header is about `text`/`html`, not `js`. Either
  way, do not rely on cross-call globals.

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

The Playwright suite is the regression gate: 28 spec files, 55 static `test()`
calls (viewport-parameterized describes multiply that). **Its coverage depends
entirely on the env**: a bare `npm run e2e` resolves ~25 passed / 35 skipped,
because every authed spec self-skips without the `E2E_*` vars. Under the full
hermetic stack it is **53 passed / 7 skipped / 0 failed** (measured 2026-08-27,
10.7m).

**As of 2026-08-27 nothing runs it automatically** — GitHub Actions is quota-dark
org-wide (the tell: runs fail in 2–4s with **zero** steps), and the Tekton jobs
that do run are `vetr-app`'s `vitest`/`typecheck`/`a11y` plus `vetr-api-pest` —
none of which is the Playwright suite. ⚠ **Re-check before relying on this**: a
workflow named `e2e (hermetic)` exists on `vetr-app` and resumes running the suite
by itself when the quota resets.

⚠ On the **laptop**, `~/.config/vetr/authnet.env` holds PROD creds while the
harness forces `ANET_ENDPOINT=sandbox`, so `e2e:mint-tokens` fails the card
provision with `E00007` and leaves `owner_has_saved_card:false`. The saved-card
specs then self-skip. Do not read a green laptop run as covering them.

So the bridge's job is the **exploratory half the suite does not cover**:
reproducing a UX report, walking a flow with no spec (pool decline/expire,
cancellation-with-fee, Rx gating, subscription purchase, admin), and eyeballing a
fix before someone writes the spec. **It is not a substitute for a spec** — when a
bridge walk finds something, the deliverable is a spec in `vetr-app/e2e/`, not a
note.

🔴 **And nothing a bridge walk finds is a vetr defect until the suite reproduces
it.** The suite renders these routes green; this file's own worst mistake was
skipping that step.
