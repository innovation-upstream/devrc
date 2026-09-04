# comics.zacx.dev — comic-flex: two lanes, a CSP that fakes a broken bridge, and a passkey nobody can automate

**Load this when:** a result envelope named this file in `site_notes` · you are
about to drive or read `comics.zacx.dev` or `comic-flex.homelab.lan` · a `js`/`eval`
came back `null` here · you are about to report that the bridge or the profile is
broken · you need the pause/next/busy-card flows · you are about to log in or out
of Authelia on the operator's live profile.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Mechanism stays in the mechanism files: throttling and `wake` →
`reference/spa-wake.md`; hit-testing → `reference/css-hit-test.md`; proving a read
is the live authenticated session → `reference/auth-pages.md`. This file is only
what is true of **comic-flex**.

App context: `homelab-talos/containers/comic-flex-pwa/`, the `comic-flex` Claude
skill, and `claudedocs/handoff-comic-flex.md`. The Pi program is a separate repo,
`github.com/ZacxDev/comic-flex`.

---

## 🔴 `js` AND `eval` RETURN `null` HERE. THE BRIDGE IS FINE.

The app deliberately withholds `unsafe-eval` in its CSP, so the injected script is
blocked and **every** `js` expression — including `1+1` — comes back
`"value": null` with `ok: true` and no error. The Authelia login page
(`login.zacx.dev`) does the same.

**Measured 2026-09-03, with the control that settles it:**

| target | `js '1+1'` |
|---|---|
| `example.com` (control) | **`value: 2`** |
| `comic-flex.homelab.lan` | `value: null` |
| `login.zacx.dev` | `value: null` |

🔴 **A session burned a whole attempt concluding "the `js`/`eval` subcommands are
INERT for this profile" from a `null` here.** They are not. It is site CSP — trap
2 in the core skill, the same as GitHub. **Never re-derive that.** If you need the
control again, run `js '1+1'` on `example.com` in the same instance; a `2` there
and a `null` here is CSP, not tooling.

**What works instead, all verified on this app:**

| need | use | why it survives CSP |
|---|---|---|
| read the DOM | `text`, `text --annotated`, `html` | injects no script |
| click anything | `click <selector>` | **trusted CDP input**, not page JS |
| type | `type`, `key` | same |
| see it | `screenshot` | CDP capture |

So there is **no** check on this app that needs page scripting. The "something must
click and we cannot click" blocker recorded in older handoffs is dead.

---

## TWO LANES. Pick one before the first op — they differ in what they can SEE.

| lane | host | auth | service worker |
|---|---|---|---|
| **A — LAN** | `http://comic-flex.homelab.lan` | **none** | 🔴 **never registers** |
| **B — public** | `https://comics.zacx.dev` | Authelia passkey | registers |

🔴 **Lane A is STRUCTURALLY BLIND to every service-worker and cache bug.** A SW
needs a secure context; plain HTTP that is not `localhost` is not one, so no worker
ever installs on the LAN host. A clean Lane A pass says nothing about a stale-shell
bug. If you need a secure context without Authelia, `kubectl port-forward` and use
`http://localhost:<port>` — that **is** a secure context.

🔴 **Lane A is the right lane for everything else.** It is the same app, the same
`app.js`, no login, no redirects, and no chance of disturbing the operator's
session. Older attempts failed by reaching for Lane B out of habit, getting `302`/
`303` to Authelia, and reporting the feature broken.

## 🔴 Authelia: a human gate, and one you can permanently break

- Login is a **passkey**. It needs a physical authenticator touch. **No agent can
  log in here.** If Lane B returns `login.zacx.dev`, the session is gone and only
  the operator can restore it — say so and stop; do not hunt for a workaround.
- 🔴 **NEVER visit `https://login.zacx.dev/logout`, and never click Sign out.** A
  dispatched agent did exactly that "to test session expiry", ended the operator's
  session, and blocked its own remaining objectives.
- Timeouts (`clusters/production/flux-system/charts/authelia/authelia.yaml`):
  `inactivity: 5m` · `expiration: 1h` · `remember_me: 1M`.
  🔴 **Remember-me beats inactivity.** With the box ticked the session survives an
  idle wait, so an expiry test can neither pass nor fail — a 6-minute idle that
  stays authenticated is the setting **working**. Any expiry test needs a session
  established with remember-me **unchecked**, which only the operator can do.
- The header htmx does not send is what gets a clean status:
  plain GET → `302` · `HX-Request: true` → `302` · `Accept: */*` → `302` ·
  **`X-Requested-With: XMLHttpRequest` → `401`**. `app.js` adds that header via
  `htmx:configRequest` and turns the 401 into a reload.

---

## The page, by selector (verified on image `0.3.3`)

Control page `/` — ids are `#hero`, `#countdown`, `#status`.

| control | selector | note |
|---|---|---|
| play/pause transport | `#status button[hx-post="/api/toggle"]` | also `button[aria-label*="slideshow"]` |
| next page | `#status button[hx-post="/api/next"]` | `aria-label="Next page"` |
| previous page | `#status button[hx-post="/api/prev"]` | |
| layout (3 buttons) | `#status button[hx-post="/api/viewmode"]` | portrait / landscape / landscape-two |
| nav | `header nav a[href="/browse"]`, `/search`, `/settings` | `hx-boost`ed |

**The glyph, which is the whole of the W1 bug:**

| state | `aria-label` | SVG children |
|---|---|---|
| playing | `Pause the slideshow` | **2 `<rect>`**, 0 `<polygon>` |
| paused | `Resume the slideshow` | 0 `<rect>`, **1 `<polygon>`** |
| scanning | — | `iconIndexing()`, a three-dot ellipsis — a distinct THIRD glyph, never "still shows pause" |

Read the glyph without page JS:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work --tab <id> html --max-bytes 200000 --wake \
  | python3 -c 'import json,sys,re; h=json.load(sys.stdin)["result"]["data"]["html"]; \
b=re.search(r"<button[^>]*aria-label=\"(Pause|Resume)[^\"]*\".*?</button>",h,re.S); \
print("MISSING") if not b else print(b.group(1), "rect",b.group(0).count("<rect"), "polygon",b.group(0).count("<polygon"))'
```

🔴 **Scope the count to the BUTTON, never to the `#status` card.** The card holds
the layout icons too, so a whole-card count reads `rect 11 polygon 2` while the
slideshow is *playing* — compare that against the 2-rect/0-polygon row above and
you get a confident false "the glyph is wrong". Measured 2026-09-03. The regex
above is anchored on the transport button's own `aria-label`, and prints
`MISSING` rather than a zero if it fails to match — a zero from a regex that
matched nothing is the same trap one level down.

🔴 **The server is the arbiter, and it is a different question from the render.**
Compare the browser against the server *at the same moment*:

```bash
curl -s http://comic-flex.homelab.lan/ui/status | grep -oE 'aria-label="(Pause|Resume)[^"]*"'
```

- server `Resume` + browser triangle ⇒ no bug.
- server `Resume` + browser two bars ⇒ **client-side**; check the worker and caches.
- server `Pause` ⇒ the Pi never paused. A different defect — the round trip, not
  the render. Say which; do not call it the glyph bug.

---

## FLOW: the "display is busy" card (503 backpressure)

🔴 **The busy card is reachable ONLY in a command response.** `GET /api/state`
cannot 503, so `/ui/status` will never show it and polling for it forever proves
nothing. Something must issue a command while the Pi's queue is full.

The Pi accepts `maxQueuedMutations = 64` plus one in flight; beyond that it returns
`503` + `Retry-After: 1`, and the PWA must classify that as backpressure rather
than a dead device.

```bash
# terminal — saturate (this DOES turn ~65 real pages on the physical display)
for i in $(seq 1 150); do curl -s -o /dev/null -X POST http://comic-flex.homelab.lan/api/next & done

# bridge, WHILE that runs — trusted click, then read the card
$BB --instance work --tab <id> click '#status button[hx-post="/api/next"]'
$BB --instance work --tab <id> text '#status' --wake
```

- **PASS:** the card reads *"The Pi is catching up… Press it again in a moment."*
- **FAIL:** *"Cannot reach the Pi"* — that is the exact defect #623 fixed.

## FLOW: pause, read the glyph, resume

```bash
$BB --instance work --tab <id> click '#status button[hx-post="/api/toggle"]'   # pause
# ... read the glyph (above) and compare against the server curl ...
$BB --instance work --tab <id> click '#status button[hx-post="/api/toggle"]'   # 🔴 RESUME
```

🔴 **It is a physical display in the operator's home. Always resume.** Confirm with
the server curl reading `Pause the slideshow` again before you finish.

## FLOW: service worker + cache inspection (Lane B or localhost only)

`Application → Service Workers` / `Cache Storage` are devtools surfaces the bridge
does not expose. What the bridge *can* do is register what the page sees; the
caches to expect are `comic-flex-shell-v3` and `comic-flex-thumbs-v2` and
**nothing else**.

🔴 **`comic-flex-shell-v2` present = the answer.** It is the poisoned cache from the
old `hx-boost` defect (an `hx-boost` nav is an XHR with `request.mode === 'cors'`,
never `'navigate'`, so `isNavigationRequest()` was false and HTML documents got
cached). The current worker must never store a page URL — any entry in
`comic-flex-shell-v3` whose URL is `/`, `/browse` or `/search` rather than
`/static/...` is the bug.

---

## Two more that have each cost a session

- 🔴 **Never diagnose a comic-flex OUTAGE from a browser read.** Use the Pi's own
  health, `/api/state`, pod status, or an anonymous `curl`. `status` in
  `scripts/comic-flex.sh` reads `config.yaml` **on disk** — runtime truth is
  `/api/state`.
- **A client-side validation error renders "Cannot reach the Pi" / 502.** Every
  non-`BusyError` failure takes that branch, so an over-long queue or a garbage
  `/api/interval` blames the device for a request the PWA rejected locally. Known,
  filed; do not report it as the device being down.
