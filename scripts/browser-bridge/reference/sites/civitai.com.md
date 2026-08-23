# civitai.com — identity, account switching, and the reads that lie

**Load this when:** a result envelope named this file in `site_notes` · you are
about to act on civitai.com **as a particular user** · you need to know WHICH
account a Brave profile holds · a `/apps` read looks empty, stale, or shows
entries that 404 · you are about to conclude civitai "leaked scope" or "is
broken" from a browser read.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
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

🔴 **Read `needsSignIn` on the target row before clicking.** A row rendered with a
`Sign in` hint has aged out of the seamless window and clicking it lands you in the
SSO gate you cannot automate — a different outcome from a silent swap, and the only
signal distinguishing them is that hint.

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

## `/apps` needs a long settle (~9s)

Cards arrive via tRPC, and a hidden tab is throttled (mechanism:
`reference/spa-wake.md`). A browser read of `/apps` needs roughly **9 s after
`wake`** before the rail is populated.

Use `document.body.innerText.length` as a **sanity floor** — about **221 chars**
means the page shell rendered and the content did **not**. Assert the floor;
never read an empty rail as "this account has no apps".

## Selectors on civitai

`data-testid` is **stripped from the production build**, so a testid selector
matches nothing and reads as a missing element — mechanism, and what to select
instead, in `reference/css-hit-test.md`. Purpose-built non-`testid` data
attributes DO survive: civitai added **`data-listing-cover-placeholder`** for
exactly this, and it is a reliable hook.

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
