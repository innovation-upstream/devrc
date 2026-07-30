# oopif-rig — an on-demand NESTED cross-origin OOPIF reproduction

The only known reliable way to reproduce a **grandchild** out-of-process iframe on
demand, for live-verifying the CDP frame ops (`eval --frame`, `upload --frame`).
It is a **manual live-verify fixture**, not part of any automated suite — the unit
tests (`tests/nested_oopif.test.mjs`) model the same shape against a mocked
`chrome.debugger`.

## The three-domain trick

Chrome's site isolation puts a **cross-origin** iframe in its own renderer/target
(an OOPIF). To get a *nested* OOPIF you need **three distinct registrable sites**
that all resolve to loopback, served by ONE server:

| level | url | site |
|-------|-----|------|
| top   | `http://127.0.0.1:8901/top.html`         | `127.0.0.1` |
| mid   | `http://lvh.me:8901/mid.html`            | `lvh.me` (public DNS → 127.0.0.1) |
| leaf  | `http://127.0.0.1.nip.io:8901/leaf.html` | `nip.io` wildcard → 127.0.0.1 |

`top` iframes `mid`; `mid` iframes `leaf` → `leaf` is a **grandchild** OOPIF.

> ⚠ **Do NOT use `vcap.me`.** It is still widely recommended as a loopback alias but
> now resolves to a **real public IP** (103.224.182.214). `lvh.me` and `nip.io` were
> the working pair as of 2026-07; re-check with `getent hosts <name>` before trusting
> either — a loopback alias going public is a live-traffic leak, not just a broken test.

Both aliases need working public DNS. Ports must match across all three (8901 here);
a different port is a different *origin* but the SAME site, which is why the port is
irrelevant to the isolation and the hostnames are what matter.

## Serve it

```bash
python3 -m http.server 8901 --bind 127.0.0.1 \
  --directory ~/workspace/devrc/scripts/browser-bridge/tests/fixtures/oopif-rig
```

## Live-verify sequence

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

**Expected AFTER the nested-OOPIF fix (#211):**

- `frames` lists all three (`leaf.parentFrameId == mid.frameId`);
- `eval --frame <mid>` → `"http://lvh.me:8901/mid.html"`, exit 0;
- `eval --frame <leaf>` → `"grandchild-reached"`, exit 0;
- `text --frame <leaf>` → contains `leaf-marker`.

**Confirmed BEFORE the fix (extension 0.2.0, live Brave):** `frames` already listed
the grandchild (webNavigation is OOPIF-aware) and `text --frame <leaf>` read it fine,
but `eval --frame <leaf>` failed with
`op 'eval' failed in the browser: frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html`
(exit 1) — because `Target.setAutoAttach` is not recursive, so the grandchild's target
never attached to the tab's top session. That is the exact defect the fix removes.
