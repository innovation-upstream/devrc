# civitai.com — identity, account switching, and the reads that lie

**Load this when:** a result envelope named this file in `site_flows` · you are
about to act on civitai.com **as a particular user** · you need to know WHICH
account a Brave profile holds · a `/apps` read looks empty, stale, or shows
entries that 404 · you are about to conclude civitai "leaked scope" or "is
broken" from a browser read · you are checking whether a change that shipped in
the last hour is live (read `buildId` — a negative read mid-rollout is worthless).

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md` (`reference/…` paths
below resolve from that bridge dir).
Mechanism files stay authoritative for mechanism: throttling and `wake` →
`reference/spa-wake.md`; hit-testing and stripped `data-testid` →
`reference/css-hit-test.md`; frames/OOPIFs → `reference/frames-cdp.md`;
proving a read is the live authenticated session → `reference/auth-pages.md`.
This file is only what is true of **civitai.com**.

---

## 🔴 Identity: never trust a written-down profile→account mapping

**The Brave-profile→account mapping has NO shelf life.** It was recorded WRONG
three times in five days, in **both** directions. Do not carry one in your head,
in a handoff, or from this file.

**ALWAYS read the session endpoint instead**, and use what it returns:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work js '(async function(){
  const r = await fetch("/api/auth/session", {credentials:"include"});
  const j = await r.json();
  return JSON.stringify({username:j?.user?.username, id:j?.user?.id,
                         isModerator:j?.user?.isModerator});
})()'
```

Cost of checking: **one request**. Cost of trusting a recorded mapping: **hours**.

`{username, id, isModerator}` is the identity. Nothing else is — not the profile
key, not the profile label, not the tab title, not what you did yesterday.

## 🔴 There IS an in-product account switcher — CHECK IT BEFORE ESCALATING

🔴 **This section used to say "there is no switch accounts flow". That was WRONG,
and it cost a whole session on 2026-08-21**: the only open check in a 13-PR arc was
declared blocked on the operator signing an account in, twice, while the account sat
in the device roster one click away. **A recorded absence is the cheapest kind of
claim to be wrong about — this file asserted it, so nobody looked.**

Civitai keeps a **device account set** (`AccountProvider`, `swapAccount`) and the
header menu can swap between its members **with no re-authentication**, provided the
entry is inside the seamless-switch window. Try this FIRST; escalate to the operator
only after the roster has been read and the account you need is genuinely not in it.

### The recipe (measured 2026-08-21, all four steps required)

🔴 **The roster is a SECOND VIEW of the menu — its accounts are NOT in the DOM until
you drill in.** Searching the opened menu for a username returns nothing, and that
reads exactly like "this profile doesn't have that account". It is not the same
claim. The drill-in trigger is the **avatar row at the top of the dropdown** (the one
showing your own username plus a `›` chevron-right).

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
TAB=…                       # from `open`; reuse it — a re-`open` discards your url
$BB --instance <key> --tab $TAB click '[aria-label="Account menu"]'
$BB --instance <key> --tab $TAB wake --wait 3500     # settle, or the popover is empty
# now tag+click the avatar row (the button whose text is your CURRENT username),
# settle again, and only THEN read the roster / tag the target row and click it.
```

After the swap, **verify identity from the server**, never from the avatar:
`nav https://civitai.com/api/auth/session` → assert `user.id` is the one you wanted
and `impersonatedBy` is absent. Then purge account-scoped `localStorage` (below).

🔴 **Read `needsLogin` on the target row before clicking.** A row rendered with a
`Sign in` hint has aged out of the seamless window and clicking it lands you in the
SSO gate you cannot automate — a different outcome from a silent swap, and the only
signal distinguishing them is that hint.

⚠ **The field is `needsLogin`; the rendered text is `Sign in`.** This file said
`needsSignIn` — a name that exists nowhere in civitai — because the *label* was
read back as the *field*. There is nothing in the DOM to grep for either: the
hint is a bare `<span class="text-xs opacity-60">Sign in</span>` with no
attribute of its own, so the only selector you have is the visible string.
Source, if you need to re-check: `needsLogin` in
`src/components/CivitaiWrapped/AccountProvider.tsx` (set to
`!(id in deviceAccounts)`) and its render in
`src/components/AppLayout/AppHeader/UserMenu.tsx`.

🔴 **A text scan for `Logout` finds nothing even though it is right there** — the
switcher's footer actions are ICON buttons with no text nodes. Do not conclude a
control is missing from a text read; look for the icon/`aria-label`.

### Profiles are still separate cookie jars

The switcher moves between accounts **already on this device/profile**. Different
Brave profiles remain independent jars, so picking the right profile is still a real
operation. Enumerate; never assume:

```bash
# every connected instance, then the session in each — read the pair, not the key
$BB health          # -> TOP-LEVEL .instances[].key (health is NOT a /cmd op,
                    #    so there is no .result.data wrapper here)
$BB --instance <key> js '(async function(){ …the fetch above… })()'
                    # -> the value lands at .result.data.value
```

### 🔴 `connected` ≠ drivable

An instance can report `connected: true` from `browser health` and still fail
`open` with `No current window`. That instance's identity is **UNKNOWN**, not
absent — do not record it as "profile X holds no account", and do not let it
narrow the candidate set. Re-check it, or hand it to the operator.

### 🔴 Sign-in is Google SSO and CANNOT be automated

Same class as the GitHub sudo-mode gate: you will not get through it. But **that is
the LAST resort, not the first** — it applies only once you have (a) read each
profile's session, AND (b) opened the account switcher's roster in the promising
one. If the account is in the roster without a `Sign in` hint, no SSO is involved.
Only when it is genuinely absent everywhere do you **STOP** and hand it to the
operator with exact steps (which profile, which account, what to confirm).

🔴 **Do NOT route around it** — not via a direct DB write, not by calling the
service layer. Those skip the side effects the UI action exists to produce
(notifications, re-queues, audit events), so the record ends up in a state the
product can never produce, and the thing you were asked to verify was never
exercised.

## Capability is DERIVED, not assumed

`__NEXT_DATA__.props.pageProps.flags` is the **server's own flag resolution for
that session**. Read it rather than guessing what an account can see:

```bash
$BB --instance work js '(function(){
  return JSON.stringify(window.__NEXT_DATA__?.props?.pageProps?.flags ?? null);
})()'
```

**Verify a switch took by re-reading BOTH** the session endpoint **and** the
flags. Either one alone can look right while the other has not moved.

## 🔴 After ANY account switch, purge account-scoped `localStorage`

`recentlyOpenedApps` is per-**BROWSER-PROFILE**, not per-account. Entries written
by the previous account still render in the `/apps` **"Recently opened"** rail and
**404 when clicked**.

This produced a false **"scope leak"** alarm: the UI was showing another account's
apps, and the server was doing nothing wrong.

🔴 **The server returning zero rows is NOT evidence the UI shows zero.** They are
different claims with different storage behind them — check the one you are
actually making.

```bash
$BB --instance work js '(function(){
  localStorage.removeItem("recentlyOpenedApps"); return "cleared";
})()'
```

## 🔴 The account menu is a TOGGLE — a JS `.click()` half-opens it

A JS `.click()` on the account menu leaves `aria-expanded="false"` and finds only
empty `.mantine-Popover-dropdown` shells. That is **indistinguishable from "the
entry is missing"**, and reads as a product bug.

- Use the **trusted `click` op** (top-frame CDP input), not a JS `.click()`.
- Click **ONCE** — it is a toggle, so a second click closes what the first opened.
- **Read `aria-expanded` in the same breath** as the click, so you know which
  state you are in rather than inferring it from what you found.
- If the dropdown links are still not selector-reachable, **`screenshot`** and
  look. Do not selector-hunt.

## Route-level positive control

- **`/apps` returns 404 for an account outside the store cohort.** A 404 there is
  a cohort fact, not a broken session.
- **`/models` returning 200 is the positive control** that the session works at
  all. Run it before diagnosing anything from an `/apps` 404.

## `/apps` needs a long settle (~9s), and `wake --wait` CANNOT give you 9s

Cards arrive via tRPC, and a hidden tab is throttled (mechanism:
`reference/spa-wake.md`). A browser read of `/apps` needs roughly **9 s after
`wake`** before the rail is populated.

🔴 **`wake --wait` is capped at 6000 ms and clamps SILENTLY** —
`WAKE_SETTLE_MAX_MS = 6000` in `extension/protocol.js`, applied by `clampWakeMs`
as a bare `Math.min`, with no warning and no error. It is not a policy number:
the ceiling is `CDP_OP_BUDGET_MS − CDP_COMMAND_TIMEOUT_MS − 1s`, so a larger
value cannot be honoured without the op timing out. Anything you write above 6000
is a 6000 you have hidden from yourself. So spend the settle in two parts:

```bash
$BB --instance work --tab "$TAB" wake --wait 6000   # the whole budget, once
sleep 4                                             # the rest, outside the op
$BB --instance work --tab "$TAB" js '…'             # then read
```

Use `document.body.innerText.length` as a **sanity floor** — about **221 chars**
means the page shell rendered and the content did **not**. Assert the floor;
never read an empty rail as "this account has no apps".

## 🔴 Verifying a just-shipped change: read `buildId`, or a NEGATIVE read is worthless

**Load this before concluding a newly-released feature "isn't live yet".**

dp-prod rolls with `maxUnavailable: 0` and warmup-gated readiness, so for
**minutes to tens of minutes** after a promotion the SSR pool serves BOTH images
at once. Measured 2026-09-04: 159 → 35 pods still on the old image over the ~12
min following promotion, and still not drained when the check ended.

That window has an asymmetry which is easy to miss:

- a **positive** read (the new behaviour is present) is conclusive the instant it
  appears — only the new code can produce it;
- a **negative** is NOT. *"The feature is correctly off for this input"* and
  *"this request hit a still-serving old pod"* explain it equally well, and
  nothing on the page distinguishes them.

Measured harm: `/apps/run/sensei` returned a bare iframe `src` **twice** before
returning the init fragment on the third try. Either of the first two, reported,
is a false regression against a feature that was already working.

**The discriminator is one field, read in the SAME expression as your
measurement** — `window.__NEXT_DATA__.buildId`. Two different values across reads
IS the mixed fleet, observed rather than inferred:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work --tab "$TAB" js '(function(){var f=document.querySelector("iframe");var s=f?f.getAttribute("src"):null;return JSON.stringify({build:(window.__NEXT_DATA__||{}).buildId,frag:s&&s.indexOf("civitai-block=v1")>-1,src:s})})()'
```

An absence carrying the **new** buildId is a fact about the feature; one carrying
the **old** buildId is a fact about pod scheduling. Retry until the buildId is the
new one, rather than waiting out the whole drain — that is what let three negative
controls close while the roll was still less than half done.

🔴 **And validate the instrument before believing any negative: find an input
that is ALREADY live on the old image and assert your read sees it there.** For
the App Blocks init fragment that was `app-requests` — allowlisted a release
earlier, so it carries the fragment on *both* images, which proves the read
technique works independently of the rollout under test. Without such a control,
*"I saw nothing"* is indistinguishable from *"my selector was wrong"*.

Generalises past App Blocks: it applies to any civitai.com read taken to confirm
a change that shipped in the last hour.

## Selectors on civitai

`data-testid` is **stripped from the production build**, so a testid selector
matches nothing and reads as a missing element — mechanism, and what to select
instead, in `reference/css-hit-test.md`. Purpose-built non-`testid` data
attributes DO survive: civitai added **`data-listing-cover-placeholder`** for
exactly this, and it is a reliable hook.

🔴 **This is a claim about the civitai.com HOST page only.** An App Block ships its
own build and can keep its testids — see the App Block section below.

---

## 🔴 Driving an App Block — `/apps/run/<slug>` is a cross-origin iframe

**Load this before the first op on an App Block.** The block is NOT part of the host
page: `/apps/run/<slug>` embeds `https://<slug>.civit.ai/` as an OOPIF. A read or
click without `--frame` addresses the civitai shell and finds nothing.

🔴 **There is a DEEPER doc for the block itself: `flows/civit.ai.md`** — the App
Block host, its boot handshake, and the 404 that looks like an app. **The bridge
will not route you there until your FIRST `--frame` op**, because `site_flows` is
keyed on the host of the result envelope's `url` (`_domain_from_result` in
`server.py`): a plain op on `/apps/run/<slug>` reports the TAB's url
(`civitai.com` → this file), and only a `--frame` op reports the FRAME's url
(`<slug>.civit.ai` → that one). So the routing arrives one step after you needed
it — read `flows/civit.ai.md` now, not when the envelope names it. This section
is the entry point; that file is the depth.

### The recipe (measured 2026-08-26 on `/apps/run/sensei`)

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work open https://civitai.com/apps/run/sensei     # lands HIDDEN
$BB --instance work --tab "$TAB" wake --wait 6000                # tab level, once — 6000 IS THE CAP
sleep 6                                                          # the rest of the settle, outside the op
$BB --instance work --tab "$TAB" frames                          # -> the block's frameId
# every subsequent read AND input op carries --frame:
$BB --instance work --tab "$TAB" --frame "$FR" text
```

🔴 **This recipe used to say `--wait 12000` — double the cap, silently clamped to
6000.** `WAKE_SETTLE_MAX_MS = 6000` (`extension/protocol.js`); `clampWakeMs` is a
bare `Math.min` with no warning. Writing a bigger number does not buy a longer
settle, it just makes the number you wrote down a fiction. Put the extra wait in
a `sleep` between ops, as above.

🔴 **The frameId is per-tab and not stable** — 437 in one run, 484 in the next, same
URL and same page. Re-run `frames`; never carry one over.

⚠ Two op-level refusals you WILL hit here; both are expected and both are already
documented in `reference/spa-wake.md` — don't re-diagnose them:
`open <url> --wake=MS` → **`cdp_attach_refused:about:`** (open succeeded, exit 3;
the tab was still `about:blank` because navigation had not committed, and
`about:` is not an attachable scheme — re-issue `wake` as its own call), and
`wake --frame <id>` → `wake_with_frame_unsupported` (un-throttling is tab-level;
wake the TAB, then re-issue the frame read).

⚠ **`cdp_attach_refused:<no-scheme>` is a DIFFERENT failure** — the url is
absent or uncommitted, so `new URL()` throws and there is no scheme to name
(`cdpSchemeOf` in `extension/protocol.js`). This file used to print that one for
the `open --wake` case, which sends you looking for a missing url that is not the
problem.

**Harvest selectors with a `js` enumeration, not `text --annotated`.** Annotated
returned 60–69 element records (~600 log lines) for this one small app; a one-line
enumeration returns every control with its testid:

```bash
$BB --instance work --tab "$TAB" --frame "$FR" js '(function(){var s="";document.querySelectorAll("button").forEach(function(b){s+=JSON.stringify({t:(b.innerText||"").trim().slice(0,30),testid:b.getAttribute("data-testid")})+"\n"});return s})()'
```

### 🔴 `data-testid` SURVIVES in an App Block's own build

The strip is a property of **civitai.com's Next build**, not of the page you are
looking at. An App Block is a separate Vite bundle, and its testids ship. Measured
inside the sensei frame: `buzz-balance`, `chat-input`, `send-button`,
`messages-container`, `session-list`, `session-item-<id>`, `new-session-button`,
`open-research`/`close-research`, `research-search-input`, `research-search-button`,
`insert-model-<modelId>`, `model-selector`, `temperature-slider`, `settings-button` —
21 buttons enumerated, every one carrying a testid, `aria-label` null on all of them.

So: **enumerate testids inside the frame before selector-hunting.** Assuming the
strip here costs you the one-line selector and pushes you onto DOM-shape paths for
nothing. (The host page's strip still stands — see `## Selectors on civitai`.)

### 🔴 A `window.fetch` hook inside the block catches NOTHING — and the reason is not the world

**The operating rule is unchanged and load-bearing:** hooking `window.fetch` to
prove a request fired **intercepts nothing** and returns an empty list that reads
exactly like *"no request was made"*. That produced one false negative on this
page. **Every observation must be a DOM observation**, and a verdict must be
built from the controls below, never from an absence of intercepted traffic.

🔴 **What this section used to give as the MECHANISM — "`js` runs in an ISOLATED
world, the DOM is shared but `window` is not" — is WRONG for a block, and it is
the load-bearing half, because it tells you the hook is impossible when in fact
it installs and still sees nothing.** `js`/`eval --frame` is the one op that does
NOT use `chrome.scripting`: it runs your string through CDP `Runtime.evaluate`
(`cdpFrameEval` in `extension/service_worker.js`), and that path forks:

- **same-process iframe** → `Page.createIsolatedWorld` → isolated, `window` not shared;
- **cross-origin OOPIF** → evaluated in the frame's own flat auto-attached
  session with **no `contextId`**, i.e. that frame's **MAIN world** — the page's
  own `window`, `fetch` included.

`/apps/run/<slug>` is the second case by this file's own first paragraph, so your
hook lands on the block's real `window.fetch`. `reference/frames-cdp.md` has had
this right all along ("same-process frames in an isolated world, a cross-origin
OOPIF in its own flat session"); only this file was wrong.

⚠ **So the empty result is UNDIAGNOSED, not explained.** Plausible rivals nobody
has separated: the request had already fired before the hook was installed, the
app uses XHR / `sendBeacon` / a worker rather than `fetch`, or the module captured
`fetch` at import time. Don't spend the budget re-deriving a mechanism — use the
DOM controls below, which do not depend on knowing which. If you *do* want the
network, `reference/frames-cdp.md` is the place to start, not a `fetch` shim.

### 🔴 Input inside `--frame` is SYNTHETIC — so it needs its own control

Every `click`/`type`/`key` under `--frame` returns `"trusted": false` (top-frame
input returns `trusted: true`). That is a permanent, plausible-sounding excuse for
any null result — *"maybe the app just ignores untrusted input"* — and it will
neutralise your verdict unless you pre-empt it.

**Never conclude an App Block is broken without a positive control that uses the SAME
synthetic path and costs nothing.** In sensei that is the Research panel: synthetic
`click [data-testid="open-research"]` → synthetic `type 'anime'` → synthetic
`key Enter` → 10 real models with live counts rendered within ~5 s
(`Anime Lineart / Manga-like … LORA · 👍 21,394 · ↓ 205,853`). With that in hand, a
null on another path is a fact about **that path**, not about your technique.

Generalise: pick a **no-cost, in-app action that round-trips through the app's own
token/network** and drive it with exactly the ops you will use on the target.

### The composer assertion — read the value AND the button state before clicking

React owns the composer. Assert both, in one `js`, immediately before the click:

```bash
$BB --instance work --tab "$TAB" --frame "$FR" js '(function(){var t=document.querySelector("[data-testid=\"chat-input\"]");var s=document.querySelector("[data-testid=\"send-button\"]");return JSON.stringify({value:t?t.value:null,sendDisabled:s?s.disabled:null})})()'
```

The `disabled` half is the load-bearing one: a click on a disabled control lands and
does nothing, silently. Measured that the read is a **moving** signal and not a
constant — `sendDisabled: true` on an empty composer, `false` after `type 'What is
DreamShaper?'` (value read back verbatim), `true` again after the send. If it is
still `true`, the input did not take: retry the `type`, do not click.

### 🔴 "The hit-test passed but the click did nothing" — two causes, both mundane

This signature is **not** a bug in the bridge and **not** an `isTrusted` gate, and
it is worth naming because both explanations below are cheap to check and neither
is where anyone looks first:

1. **The tab got RE-throttled.** A reload re-throttles it, and clicks then go
   silently inert — mechanism in `reference/spa-wake.md` ("After a RELOAD the tab
   is RE-THROTTLED") and the one-liner in the bridge `SKILL.md`. Nothing in this
   file used to mention it, so the App Block recipe above reads as though the
   single `wake` holds for the session. It does not: **re-`wake` after any
   navigation or reload**, then re-run `frames` (the frameId changed too).
2. **The control is genuinely `disabled`.** The composer case above is one
   instance; the scope-consent dialog is the other, and it is the one that
   catches people. `BlockConsentModal`'s **Allow** button is
   `disabled={budgetBlocksSubmit}`, where
   `budgetBlocksSubmit = grantsSpend && limitEnabled && !budgetValid` — so for a
   spend-scoped app with the daily limit toggled on, **Allow stays dead until a
   valid integer budget is in the field**, between
   `BLOCK_CONSENT_BUDGET_MIN_PER_DAY` and `BLOCK_CONSENT_BUDGET_MAX_PER_DAY`. A
   hit-test passes on it happily. ⚠ That dialog belongs to the **host page**, not
   the block — drive it with a **top-frame** `click`, with no `--frame`, or you
   will hunt for it inside the iframe and correctly find nothing. Read
   `.disabled` before every click you intend to draw a conclusion from.

### 🔴 Three controls that turn a null into a verdict

A "nothing happened" is the observable that the most causes share. Before reporting
one, run all three — each rules out a different mechanism, and together they cost
under a minute:

1. **Positive control on the same synthetic path** (above) — separates *"the app is
   broken"* from *"my input never reached it"*.
2. **Type-but-do-not-send** — proves the composer does not spontaneously clear, so a
   clear *on send* is genuinely the handler firing rather than a re-render or
   throttling artifact. Measured: `type 'persist test'` → read back `persist test`;
   after `sleep 12`, still `{"value":"persist test","sendDisabled":false}`.
3. **Whole-DOM string search for what you typed** — a bubble can render outside the
   container you are watching. `js` →
   `{hasHello:false, hasDreamShaper:false, bodyLen:9717}` is a much stronger claim
   than an unchanged `messages-container`.

Pin a **numeric** before/after alongside them (`[data-testid="buzz-balance"]` here —
read it once up front and assert it is unchanged across the whole run) and sweep for
error surfaces the container would not show:

```bash
$BB --instance work --tab "$TAB" --frame "$FR" js '(function(){var o=[];document.querySelectorAll("[role=alert], .error, [class*=error], [class*=toast], [class*=notification]").forEach(function(e){o.push((e.innerText||"").trim().slice(0,100))});return JSON.stringify({errors:o})})()'
```

### What this pattern rules out — and what it does not

Worked example (sensei chat send, 2026-08-26 — a product state, expected to change;
the method is the durable part): send handler observably fires (composer clears,
`sendDisabled` returns to `true`), no user bubble, no reply, no error, balance
unmoved, over ~70 s and two send routes (button and `key Enter`).

- **Ruled out:** "synthetic input doesn't reach the app" (control passed on the same
  ops) · "the button was dead" (`disabled:false` asserted immediately before, and the
  composer cleared on click) · "no session was active" (`+ New` created a 4th session,
  `sessionCount: 4`) · "the reply just hadn't arrived" (`messages-container`
  `innerHTML.length` constant at 259 across ~70 s) · "it rendered somewhere else"
  (whole-DOM search).
- **NOT ruled out:** an `isTrusted`-gated branch on the specific handler under test.
  Synthetic input reaching *one* path is not proof it reaches *another*. Say so in the
  report rather than claiming a slam dunk.

---

## Proposed helper (NOT implemented): `browser sessions`

**Status: a proposal, not a shipped op.** The loop below works today and is what
to paste until someone builds the op.

The gap it closes: reading `/api/auth/session` across **all** connected instances
was hand-rolled **twice in one session**, and **both times contradicted the
written-down mapping**. That is a mechanism-shaped problem, not a discipline one —
the one-call version should print, per instance:

```
key → username → id → isModerator
```

…including an explicit `UNKNOWN` row for an instance that is `connected` but not
drivable (see above), because a silently-omitted instance is how a candidate set
gets narrowed to the wrong profile.

Until then:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
KEYS=$("$BB" health | python3 -c 'import json,sys
print("\n".join(i["key"] for i in json.load(sys.stdin)["instances"]))')
for k in ${=KEYS}; do          # ${=…} — zsh does NOT word-split a bare $VAR
  printf '%s → ' "$k"
  "$BB" --instance "$k" js '(async function(){
     const r = await fetch("/api/auth/session", {credentials:"include"});
     const j = await r.json();
     return (j?.user?.username ?? "ANON") + " → " + (j?.user?.id ?? "-") +
            " → " + (j?.user?.isModerator ?? false);
   })()' 2>/dev/null \
    | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["data"]["value"])
except Exception: print("UNKNOWN (connected but not drivable?)")'
done
```

⚠ That loop reads whatever page each instance's tab is on — a session fetch is
same-origin, so point each instance at a civitai.com tab first, or it reports the
session of some other site (i.e. nothing).

---

## 🔴 The resource picker: NEVER read results without asserting the input first

**Load this before driving any civitai search box** — the generation resource
picker ("Choose a checkpoint", opened by an App Block's `pick-checkpoint`, and the
same component elsewhere).

`browser type` **replaces** the input's value correctly — but it **intermittently
does not apply at all**, and the modal keeps rendering the PREVIOUS query's
results. So a read taken right after `type` can be the *last* query's answer
wearing the new query's name. This is silent: no error, a well-formed result list,
plausible model names.

Measured 2026-08-21, driving `custom-generators`: probing `Realistic Vision` then
`ReV Animated` returned **byte-identical hits**, and probing `Pixel Art` returned
`DreamShaper|Babes` — the results of a `Dream` control run two calls earlier. The
input read back `"Realistic Vision"` while the transcript claimed `ReV Animated`.

🔴 **Three false conclusions came out of this before it was caught**, each of which
reads like a platform bug and none of which was one:

1. *"the picker's search is broken"* — the positive control `Qwen` "failed" while
   `Qwen-Image` sat visibly in the list. It was a stale read, not a broken search.
2. *"model X is not generation-covered"* — derived from a search that never ran.
3. *"`#4137` has a second face that returns an empty set"* — nearly filed upstream
   against a real issue. **It does not.** Retracted.

### The flow — clear, type, ASSERT, only then read

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
SEL='.mantine-Modal-content input[placeholder="Search models"]'
# 1. clear via the native setter + input event (React-visible)
$BB --instance work --tab "$TAB" js '(function(){var i=document.querySelector('"'"'.mantine-Modal-content input[placeholder="Search models"]'"'"');var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;s.call(i,"");i.dispatchEvent(new Event("input",{bubbles:true}));return "cleared"})()'
# 2. type, then 3. READ THE VALUE BACK and retry until it equals the query
# 4. only now wait for debounce (~3s) and read results
```

**Bind the assertion to the result read itself** — return `input.value` alongside
the hits in the SAME `js` expression and assert they match, so a drift between the
two calls cannot slip through. Retry the type up to ~5×; report
`INPUT-NEVER-TOOK` rather than reporting an absence.

### An absence needs an ADJACENT passing control

The corpus read is *also* flaky on its own (see below), so `No models found` is
only a reading when a control query that MUST return hits passes immediately
before **and** after it. Interleave `Dream` → `DreamShaper|Babes` between targets;
report the pair, never the bare absence.

### Two failures here are REAL and are not the above

- **`Couldn't load models` on the ALL tab with no search at all** — observed
  directly (error string rendered), and the modal's own **Retry** button clears it.
  Transient, recoverable; not search-specific.
- **The list is VIRTUALISED.** Only ~16–18 rows are in the DOM at any scroll
  position, so `innerText` enumeration silently caps there and reads as "the corpus
  is 18 models". The real scroller is `.mantine-Modal-content .scroll-area`
  (`scrollHeight` 12k–51k px, infinite-loading) — a scroll targeting anything else
  leaves `scrollTop: 0` while appearing to work. **Never quote a corpus size from
  an innerText count; search with the asserted flow instead.**

### What the corpus actually contains (2026-08-21)

Base/API models (Qwen-Image, FLUX.1 Kontext, Imagen 4, Krea 2, Z Image Turbo, …)
plus popular general-purpose community checkpoints (DreamShaper, PerfectDeliberate,
Pony Diffusion V6 XL, Illustrious merges). **It holds NO style-specific
checkpoints** — `coloring`, `pixel`, `line art`, `product`, `storybook` and
`sticker` all return ABSENT against a passing control. Consequence for App Blocks:
a "style" generator must be a general checkpoint **plus a style prompt template**;
pinning a style checkpoint is not achievable on this platform.

`Filters → Base model` is a working, non-search way to narrow (SD 1.5, SDXL 1.0,
SDXL Lightning, Pony, Illustrious, … are all offered) — useful when search is in
one of its flaky windows.
