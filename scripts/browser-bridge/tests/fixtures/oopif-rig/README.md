# oopif-rig — on-demand NESTED cross-origin OOPIF reproductions

The only known reliable way to reproduce **nested** out-of-process iframes on demand, for
live-verifying the CDP frame ops (`eval --frame`, `upload --frame`). These are **manual
live-verify fixtures**, not part of any automated suite — the unit tests
(`tests/nested_oopif.test.mjs`) model the same shapes against a mocked `chrome.debugger`,
which is exactly why the two questions below need a real browser to settle.

There are four rigs:

- **wake** (`wake-rig.html`) — a single **throttle-sensitive** page (no iframes). It only
  swaps in its `WAKE-RIG-RENDERED` sentinel after **30 real animation frames**, and a
  background tab gets none — so it is the only fixture here that can demonstrate the
  `wake` op / `--wake` reads. The OOPIF pages below render fine while hidden and
  therefore prove nothing about un-throttling. `window.__rig = {raf,timer,rendered,ms}`
  is the machine-readable counter set. See *Verifying `wake`* at the end.
- **wake-shadow** (`wake-shadow.html`) — a page that installs a MAIN-WORLD
  `outerHTML` getter and shadows `innerText`/`querySelector`. `text`/`html --wake`
  read via chrome.scripting's **isolated** world, so the poison must NOT appear;
  `js --wake` runs in the main world (same as plain `js`) and WILL show it. That
  contrast is the check.
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

1. **`vcap.me` now resolves to a REAL PUBLIC IP** (a parking address — `getent hosts vcap.me`
   to see today's). It is still widely
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

**Expected AFTER the nested-OOPIF fix (#211) — CONFIRMED live, run #2 below:** `frames` lists all three
(`leaf.parentFrameId == mid.frameId`); `eval --frame <mid>` →
`"http://127.0.0.1.sslip.io:8901/mid.html"`; `eval --frame <leaf>` → `"grandchild-reached"`;
`text --frame <leaf>` contains `leaf-marker`. All exit 0.

**Confirmed BEFORE the fix (extension 0.2.0, live Brave):** `frames` already listed the
grandchild (webNavigation is OOPIF-aware) and `text --frame <leaf>` read it fine, but
`eval --frame <leaf>` failed
`op 'eval' failed in the browser: frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html`
(exit 1) — `Target.setAutoAttach` is not recursive, so the grandchild's target never
attached to the tab's top session. That is the defect the fix targets.

### ✅ Live run #2 (2026-07-30, build `34769a0`, profile `personal`) — KNOWN-GOOD BASELINE

This is what a healthy run looks like. If a future change breaks the cascade, diff against
this.

**Check A (3-level rig):** `eval --frame <leaf>` (GRANDCHILD) → `"grandchild-reached"`,
reported url `http://127.0.0.1.nip.io:8901/leaf.html`; direct-child control → `"mid"`.

**Check B (7-level deep rig):** all 7 frames enumerated, parent chain
`0→117→118→119→120→121→122`, alternating `127.0.0.1` / `127.0.0.1.sslip.io`.

| frame | depth | result |
|---|---|---|
| 119 | 3 | ✅ `"deep3-reached"` |
| 121 | 5 | ✅ `"deep5-reached"` (AT the cap — resolves) |
| 122 | 6 | ✅ `oopif_depth_cap:5` (PAST the cap — refused, loudly) |

The depth-6 trace, verbatim:

```
cascade[exit=depth-cap attach=top>5B1F392F7DE254F9DECE8B1380A8211B>CD40054CBE960109E11F39916A3C40AE>DB842E0A84C5962706427874954E2992>98CEE272B651097F0CBFB4F90775F611 events=5 accepted=5 filter=on caps=d5/t50]
```

Read it: **four chained sub-session `setAutoAttach` calls** after `top` → the cascade
descends through every permitted level. `events=5 accepted=5` with `exit=depth-cap` → all
five nested targets were attributed correctly and the sixth was refused. **`OOPIF_MAX_DEPTH
= 5` is therefore a measured guarantee, not a contingent one**, and `filter=on` confirms
Chrome accepts the experimental `filter:[{type:"iframe"}]` param while real OOPIFs pass
the type gate.

**Also measured:** the ↻ reload **did** take this time (after an earlier full restart in
the same Brave session), so ↻ is worth trying first — but only because the `cascade[…]`
trace is a deterministic tell for whether it took (old build → bare `frame_not_found`; new
build → error **plus** trace). Without such a tell, do the full restart.

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

**Cause — CONFIRMED by run #2:** the own-tab check required `source.tabId` on every
`attachedToTarget`, and **Chrome does not populate it for SUB-session events** (they carry
`sessionId` only). Every level-2+ event was therefore dropped as foreign. Ownership now
falls back to **session parentage** — an event whose `source.sessionId` is a session this
cascade itself attached is ours — which keeps the own-tab invariant without depending on
`tabId` being present. `OOPIF_SETTLE_MS` was also raised (300 → 600 ms) and the quiet
window now restarts on each newly-issued `setAutoAttach`, in case a slow level was being
cut off too. Run #2 proves the parentage fallback is what fixed it.

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


## Verifying `wake` (the non-intrusive un-throttle)

`wake-rig.html` is the fixture for `browser wake` / `--wake`. It must show BOTH that a
hidden tab renders AND that **focus does not move** — a fix that renders the page but
takes the operator's screen is a failure.

```bash
export DISPLAY=:0 XAUTHORITY=/home/zach/.Xauthority
python3 -m http.server 8901 --bind 127.0.0.1 --directory "$(dirname "$0")" &

nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # BEFORE

browser open http://127.0.0.1:8901/wake-rig.html
browser text            # WAKE-RIG-SHELL, hidden:true, the wake note
browser wake            # woke:true, visibilityState:"visible"
browser text            # WAKE-RIG-RENDERED  (the DOM survived the detach)
browser js 'JSON.stringify(window.__rig)' --wake   # raf>=30, rendered:true

nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # AFTER — MUST MATCH
browser close
```

**Reference measurement** (throwaway Brave 1.89 under Xvfb, raw CDP, background tab):
baseline rAF **0/s**, timers 8/s, `hidden`; after
`Emulation.setFocusEmulationEnabled({enabled:true})` rAF **62/s**, timers 247/s,
`visible`; after detach it reverts to 0/s + `hidden`, but the rig's counters still read
`{raf:30, rendered:true, ms:472}` — the un-throttled *state* does not survive detach, the
rendered *DOM* does. `Page.setWebLifecycleState({state:"active"})` alone changed nothing
(it is only for a FROZEN page).

### Reproducing F2 on demand — the auto-wake CURE, made falsifiable

F2 (the one measured failure in the 2026-07-31 deepseek run) is *"the agent read an
unrendered shell and reported the shell text as the answer"*. It later stopped
reproducing, and the leading hypothesis was that **the rig renders before the agent's
slower read lands**, i.e. that the fixture needed a longer render deadline.

**That hypothesis is WRONG — measured 2026-08-01, laptop `.155` / `personal` /
extension 0.7.0 / `fc92ccc`, deployed artifacts byte-identical to HEAD.** The rig has no
render deadline to outlast: a hidden tab gets **no** compositor frames, so the counter
never advances and the shell is durable indefinitely. Polled every 5 s from t=0 s to
**t=36 s** with no wake:

```
raf: 1  (constant)      rendered: false      #app: "WAKE-RIG-SHELL (waiting for frames)"
timer: 7 → 43           vis: "hidden"        (≈1 Hz — the throttled timer channel, still alive)
```

`raf` stops at **1** (the single commit-time frame) and never moves. **This fixture is
already the ≥30 s unrendered shell**; nothing needs building. Run that poll first — it is
the harness's own negative control, and it is what makes a green result mean anything.

#### The 2×2 is really a 1×3 — auto-wake is behind the op allowlist

`BROWSER_AGENT_ALLOWED_OPS` gates the tool-initiated wake exactly like a model-initiated
one (`opencode/tools/browser_tool_impl.mjs:948-950` — deliberate, so a narrowed allowlist
cannot be bypassed). So **"auto-wake ON + `wake` denied" is unreachable**: denying `wake`
disables auto-wake too. Three cells, not four:

| # | condition | answer | status | steps |
|---|---|---|---|---|
| A | default (auto-wake on, `wake` allowed) | `WAKE-RIG-RENDERED` | `ok` ✅ | 3 |
| B | `BROWSER_AGENT_AUTO_WAKE=0`, `wake` allowed | `WAKE-RIG-RENDERED` | `ok` ✅ | 5 |
| C | `BROWSER_AGENT_ALLOWED_OPS=nav,text,html,eval` (⇒ auto-wake refused too) | `WAKE-RIG-SHELL (waiting for frames)` | `partial` | 4 |

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
GOAL='Open http://127.0.0.1:8901/wake-rig.html and report the EXACT text content of the element with id "app". Quote it verbatim.'
BROWSER_AGENT_KEEP_SCRATCH=1 $BB agent "$GOAL" --instance personal --allow-domains 127.0.0.1 --steps 8 --timeout 120
cat /tmp/browser-agent.*/tool-audit.jsonl     # the discriminator — see below
```

⚠ `BROWSER_AGENT_AUDIT` is **forced** to `$SCRATCH/tool-audit.jsonl` by `browser-agent:413`
and the scratch dir is `rm -rf`'d on every exit path — setting `BROWSER_AGENT_AUDIT`
yourself does nothing. `BROWSER_AGENT_KEEP_SCRATCH=1` is the only way to read the trail.

#### What the audit trail settles

The answer text alone cannot tell you *why* a cell passed. The trail can. Cell **A**:

```
exec nav → exec text → auto_wake text "hidden" → auto_wake_exec wake
  → auto_wake_exec text "re-read" → auto_wake_ok "woke:true settleMs:1500" → exec eval
```

The model's **first** `text` came back rendered; it never issued a wake itself. Cell **B**
shows the contrast — `exec text` (shell), then the model's **own** `exec wake`, then a
second `exec text`. So on this goal **the model self-cures when it can**, and auto-wake's
measured contribution is the 2 steps and ~4 s it saves, not the correctness of the answer.

**What IS now proven:** the read auto-wake intercepted would otherwise have returned a
shell — established independently twice (the 36 s poll, and cell C returning that exact
shell string). The mechanism was already live-verified; **the cure is now verified end to
end against a rig that cannot render on its own.**

**What is still NOT reproduced: the original F2 failure *shape*.** F2 was a confident
wrong **`ok`**. Cell C is the same unrendered read and the model handled it *correctly* —
`status:"partial"`, and evidence naming the cause (`op_not_allowed:wake`, "the hidden tab
is throttled"). So auto-wake is demonstrated as *"turns a shell read into a rendered
read"*, **not** as *"prevents a confident wrong answer"* — that failure mode did not recur
here, and one goal at one model version is not a general claim about it.

### Main-world shadowing check (`wake-shadow.html`)

Only the *un-throttle* is CDP — `text`/`html --wake` still perform the READ through
`chrome.scripting` (isolated world), inside the still-attached wake session. A
main-world read could be served attacker-authored content that is not in the DOM.

```bash
browser open http://127.0.0.1:8901/wake-shadow.html
browser html --wake     # MUST be the FULL document containing WAKE-SHADOW-REAL —
                        # NOT the short `<html>WAKE-SHADOW-POISON-MAIN-WORLD</html>`
browser text --wake     # MUST be exactly WAKE-SHADOW-REAL
browser js 'document.documentElement.outerHTML' --wake   # WILL show the POISON — expected
browser close
```

⚠ "must not contain POISON" is the WRONG check for `html --wake`: the fixture's own
source contains that word (in its comment and its script), so a **correct**
isolated-world read of the full document legitimately includes it. The discriminator
is that the read returns the whole document rather than the short shadowed string.

The last line is not a failure: `eval` means "run my JS with the page's own globals",
and plain `eval` is already `world:"MAIN"`, so `--wake` adds no exposure there.
