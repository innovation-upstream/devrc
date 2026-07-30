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
| basic | mid | `http://lvh.me:8901/mid.html` | `lvh.me` |
| basic | leaf | `http://127.0.0.1.nip.io:8901/leaf.html` | `nip.io` |
| deep | 0…6 | `deep0.html` … `deep6.html` | **alternating** `127.0.0.1` / `lvh.me` |

The deep rig only needs **two** domains: alternating them means every frame is still
cross-site from its **parent**, so each is its own local frame root → its own target.
(A level-2 `127.0.0.1` frame may share a *process* with the top frame — that is fine and
expected; what matters for CDP is that it is a separate local root from its `lvh.me`
parent.)

> ⚠ **Do NOT use `vcap.me`.** It is still widely recommended as a loopback alias but now
> resolves to a **real public IP** (103.224.182.214). `lvh.me` and `nip.io` were the
> working pair as of 2026-07; re-check with `getent hosts <name>` before trusting either
> — a loopback alias going public is a live-traffic leak, not just a broken test.

Both aliases need working public DNS. The port is irrelevant to isolation (a different
port is a different *origin* but the same *site*) — the hostnames are what matter.

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
`"http://lvh.me:8901/mid.html"`; `eval --frame <leaf>` → `"grandchild-reached"`;
`text --frame <leaf>` contains `leaf-marker`. All exit 0.

**Confirmed BEFORE the fix (extension 0.2.0, live Brave):** `frames` already listed the
grandchild (webNavigation is OOPIF-aware) and `text --frame <leaf>` read it fine, but
`eval --frame <leaf>` failed
`op 'eval' failed in the browser: frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html`
(exit 1) — `Target.setAutoAttach` is not recursive, so the grandchild's target never
attached to the tab's top session. That is the exact defect the fix removes.

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
