# oopif-rig — on-demand NESTED cross-origin OOPIF reproductions

The only known reliable way to reproduce **nested** out-of-process iframes on demand, for
live-verifying the CDP frame ops (`eval --frame`, `upload --frame`). These are **manual
live-verify fixtures**, not part of any automated suite — the unit tests
(`tests/nested_oopif.test.mjs`) model the same shapes against a mocked `chrome.debugger`,
which is exactly why the two questions below need a real browser to settle.

There are two rigs:

- **basic** (`top` → `mid` → `leaf`) — proves a grandchild OOPIF is reachable at all.
- **deep** (`deep0` … `deep6`) — **discriminates whether Chrome tags flat-mode events
  with the parent `sessionId`**, i.e. whether `OOPIF_MAX_DEPTH` actually binds.

## The multi-domain trick

Chrome's site isolation puts a **cross-site** iframe in its own renderer, and CDP creates
a **target** for every frame whose parent is in a different process. To nest OOPIFs you
need several distinct **registrable sites** that all resolve to loopback, served by ONE
server.

| rig | level | url | site |
|-----|-------|-----|------|
| basic | top | `http://127.0.0.1:8901/top.html` | `127.0.0.1` |
| basic | mid | `http://127.0.0.1.sslip.io:8901/mid.html` | `sslip.io` |
| basic | leaf | `http://127.0.0.1.nip.io:8901/leaf.html` | `nip.io` |
| deep | 0…6 | `deep0.html` … `deep6.html` | **alternating** `127.0.0.1` / `127.0.0.1.sslip.io` |

The deep rig only needs **two** domains: alternating them means every frame is still
cross-site from its **parent**, so each is its own local frame root → its own target.
(A level-2 `127.0.0.1` frame may share a *process* with the top frame — that is fine and
expected; what matters for CDP is that it is a separate local root from its `127.0.0.1.sslip.io`
parent.)

Both aliases need working public DNS. The port is irrelevant to isolation (a different
port is a different *origin* but the same *site*) — the hostnames are what matter.

### ⚠ Two traps when picking a loopback alias

1. **`vcap.me` now resolves to a REAL PUBLIC IP** (103.224.182.214). It is still widely
   recommended as a loopback alias; it is not one any more. A loopback alias going public
   is a live-traffic leak, not just a broken test. **Always** `getent hosts <name>` before
   trusting any of these — including `sslip.io`/`nip.io`, which were verified pointing at
   127.0.0.1 on 2026-07-30.
2. **Wallet/security extensions blocklist these domains.** The rig originally used
   `lvh.me`; on the `work` Brave profile **Phantom Wallet hijacked the tab** to
   `chrome-extension://…/phishing.html?origin=http%3A%2F%2Flvh.me…` because `lvh.me` is on
   its phishing blocklist — so the fixture silently became a phishing interstitial instead
   of the rig, which is worse than having no fixture. `lvh.me` was replaced with
   `127.0.0.1.sslip.io` for this reason. If a rig page ever renders as an extension
   warning page, **check the tab's real URL before debugging anything else**, and prefer a
   profile without wallet extensions.

## Serve it

```bash
python3 -m http.server 8901 --bind 127.0.0.1 \
  --directory ~/workspace/devrc/scripts/browser-bridge/tests/fixtures/oopif-rig
```

## Check A (basic) — is a grandchild OOPIF reachable, and is `type:"iframe"` right?

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami                                   # which host + profile am I driving?
TAB=$($BB open http://127.0.0.1:8901/top.html | python3 -c 'import json,sys;print(json.load(sys.stdin)["tabId"])')
$BB --tab $TAB activate                      # un-throttle so the nested frames actually load
$BB --tab $TAB frames                        # expect THREE frames: top(0) → mid → leaf
$BB --tab $TAB eval --frame <mid-id>  'location.href'      # DIRECT child  — worked pre-fix
$BB --tab $TAB eval --frame <leaf-id> 'window.RIG_SECRET'  # GRANDCHILD    — the fix
$BB --tab $TAB text --frame <leaf-id>                      # non-CDP path — worked pre-fix
$BB --tab $TAB close
```

Frame ids are assigned per load — read them out of `frames`, don't hardcode.

**Expected AFTER the nested-OOPIF fix (#211):** `frames` lists all three
(`leaf.parentFrameId == mid.frameId`); `eval --frame <mid>` →
`"http://127.0.0.1.sslip.io:8901/mid.html"`; `eval --frame <leaf>` → `"grandchild-reached"`;
`text --frame <leaf>` contains `leaf-marker`. All exit 0.

**Confirmed BEFORE the fix (extension 0.2.0, live Brave):** `frames` already listed the
grandchild (webNavigation is OOPIF-aware) and `text --frame <leaf>` read it fine, but
`eval --frame <leaf>` failed
`op 'eval' failed in the browser: frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html`
(exit 1) — `Target.setAutoAttach` is not recursive, so the grandchild's target never
attached to the tab's top session. That is the defect the fix targets.

### 📓 Live run #1 (2026-07-30, Brave restarted 16:21:35, extension written 16:01:21) — the cascade was INERT

The first cascade implementation **did not work in real Brave**, and the two rigs are what
proved it. Recorded here so the next run knows what "already ruled out" means:

| check | result |
|---|---|
| basic `eval --frame <mid>` (depth 1) | ✅ `"http://…/mid.html"` |
| basic `eval --frame <leaf>` (depth 2) | ❌ `frame_not_found` |
| basic `text --frame <leaf>` (control) | ✅ reads fine |
| deep `eval --frame` depth 1 | ✅ `"deep1-reached"` |
| deep `eval --frame` depth 2 and depth 6 | ❌ `frame_not_found` |

**What that ruled OUT:** depth 1 resolving means real OOPIF targets ARE typed `iframe`
(the type filter is not eating them), the scheme check is fine, and the top-session
`setAutoAttach` works. **What it ruled IN:** the failure was always `frame_not_found` and
**never `oopif_depth_cap`** — so no level-2 session was ever *recorded*. The recursion was
inert, not capped.

**Diagnosed cause (fix in this branch, awaiting re-verification):** the own-tab check
required `source.tabId` on every `attachedToTarget`, and Chrome appears not to populate it
for **sub-session** events (they carry `sessionId` only). Every level-2+ event was
therefore dropped as foreign. Ownership now falls back to **session parentage** — an event
whose `source.sessionId` is a session this cascade itself attached is ours — which keeps
the own-tab invariant without depending on `tabId` being present. `OOPIF_SETTLE_MS` was
also raised (300 → 600 ms) and the quiet window now restarts on each newly-issued
`setAutoAttach`, in case a slow second level was also being cut off.

**Every failure now carries a bounded `cascade[…]` readout**, so run #2 does not need
guesswork. Example shape:

```
frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html cascade[exit=settle attach=top>S_MID events=3 accepted=2 filter=on caps=d5/t50]
  #1 accept type=iframe tab=match  parent=absent  d=1 http://127.0.0.1.sslip.io:8901/mid.html
| #2 drop:type type=worker tab=match parent=absent      http://x/w.js
| #3 accept type=iframe tab=absent parent=known   d=2 http://127.0.0.1.nip.io:8901/leaf.html
```

How to read it on the next live run:

- **`attach=top` only** → we never descended; look at why no child was queued.
- **`attach=top>S_MID` but no `#…` row with `parent=known`** → the level-2 events are not
  reaching us at all (a `setAutoAttach`-on-sub-session problem, hypothesis 2 — try
  `Target.setDiscoverTargets` or a different param shape).
- **rows with `drop:foreign-tab` and `tab=absent`** → impossible by construction now, but
  `drop:unowned` + `parent=unknown` would mean the parentage fallback is not recognising
  our own sessions.
- **`exit=settle` with an `accept` row at `d=2`** → the frame WAS found but its url did not
  match; compare the `#…` url against the one in the `frame_not_found:` prefix.
- **`exit=deadline`** → raise `OOPIF_WAIT_MS`; **`filter=rejected→off`** → Chrome refused
  the experimental `filter` param (harmless, the listener-side type check still applies).

### Why this ALSO settles the target-`type` question (not decorative)

The resolver now hard-filters discovered targets to `targetInfo.type === "iframe"` (and
asks Chrome for `filter:[{type:"iframe"}]`), so a page cannot mint a
`new Worker(location.href)` target with an identical url and thereby either deny service
(forced `ambiguous_frame`) or capture the operator's JS in a worker global.

That filter is **load-bearing on an assumption**: that real cross-origin iframe targets
really are typed `iframe`. Check A tests it end to end — **if Chrome used any other type
string for an OOPIF target, the filter would drop the grandchild and Check A would fail
with `frame_not_found`.** So a PASSING Check A *is* the confirmation; there is nothing
extra to eyeball. If Check A fails with `frame_not_found` while `text --frame <leaf>`
still works, suspect the type filter first: temporarily log `targetInfo.type` in
`onEvt` (`extension/protocol.js`), FULLY restart Brave (↻ is unreliable), re-run, and
read the real value before changing anything.

## Check B (deep) — does `OOPIF_MAX_DEPTH` actually bind?

Depth attribution assumes flat mode tags a sub-session's event `source` with its parent
`sessionId`. **Unverified.** If Chrome does not tag it, every target reads as depth 1,
`depthCapHit` is never set, and the advertised depth cap silently stops binding (the
descent is then bounded only by `OOPIF_MAX_TARGETS` and `OOPIF_WAIT_MS`). It cannot
mis-route JS — selection is by URL equality and never consults depth — but the documented
bound would be fiction. `deep0…deep6` discriminates it cleanly.

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
TAB=$($BB open http://127.0.0.1:8901/deep0.html | python3 -c 'import json,sys;print(json.load(sys.stdin)["tabId"])')
$BB --tab $TAB activate
$BB --tab $TAB frames                  # expect SEVEN frames, deep0(0) … deep6, each nested in the last
$BB --tab $TAB eval --frame <deep5-id> 'window.RIG_DEEP'   # depth 5 — AT the cap
$BB --tab $TAB eval --frame <deep6-id> 'window.RIG_DEEP'   # depth 6 — PAST the cap
$BB --tab $TAB text  --frame <deep6-id>                    # control: non-CDP path
$BB --tab $TAB close
```

Read the outcome off `deep6`:

| `eval --frame <deep6>` result | verdict |
|---|---|
| **`oopif_depth_cap:5`** (exit 1) | ✅ depth attribution WORKS; the cap binds exactly as documented. This is the expected outcome. |
| **`"deep6-reached"`** (exit 0) | ❌ depth attribution is BROKEN (untagged `source.sessionId`) — the cap does not bind. Not a security hole (still bounded by 50 targets / 3 s, and the frame reached is the one asked for), but SKILL.md/README must stop advertising a depth of 5 and the resolver should attribute depth another way (e.g. from the webNavigation parent chain, which the caller already has). |
| **`frame_not_found:…`** | ambiguous — the rig did not nest as expected. Check that `frames` shows all 7 with a proper `parentFrameId` chain; if `deep2`/`deep4` are missing, the two-domain alternation is not producing separate targets on this Chrome and the rig needs more distinct domains. |

`eval --frame <deep5>` should return `"deep5-reached"` in the healthy case — a frame **at**
the cap resolves; only **past** it is refused. `text --frame <deep6>` should always work
(chrome.scripting path, unaffected by any of this) — it is the control proving the frame
exists and is readable, so a `deep6` eval failure is about the CDP cascade, not the page.
